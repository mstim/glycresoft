# -*- coding: utf-8 -*-
import logging
try:
    logger = logging.getLogger("target_decoy")
except Exception:
    pass
import math

from collections import defaultdict, namedtuple

import numpy as np


ScoreCell = namedtuple('ScoreCell', ['score', 'value'])


class NearestValueLookUp(object):
    def __init__(self, items):
        if isinstance(items, dict):
            items = items.items()
        self.items = sorted([ScoreCell(*x) for x in items if not np.isnan(x[0])], key=lambda x: x[0])

    def _find_closest_item(self, value):
        array = self.items
        lo = 0
        hi = len(array) - 1

        if np.isnan(value):
            return lo

        if lo == hi:
            return lo

        while hi - lo:
            i = (hi + lo) / 2
            x = array[i][0]
            if x == value:
                return i
            elif (hi - lo) == 1:
                return i
            elif x < value:
                lo = i
            elif x > value:
                hi = i

    def get_pair(self, key):
        return self.items[self._find_closest_item(key) + 1]

    def __len__(self):
        return len(self.items)

    def __repr__(self):
        return "{s.__class__.__name__}({size})".format(
            s=self, size=len(self))

    def __getitem__(self, key):
        ix = self._find_closest_item(key)
        ix += 1
        if ix >= len(self):
            ix = len(self) - 1
        try:
            pair = self.items[ix]
        except IndexError:
            print("IndexError in %r with index %r and query %r" % (self, ix, key))
            print(self.items)
            raise
        if pair[0] < key:
            return 0
        return pair[1]


class ScoreThresholdCounter(object):
    def __init__(self, series, thresholds):
        self.series = sorted(series, key=lambda x: x.score)
        self.thresholds = sorted(set(np.round((thresholds), 10)))
        self.counter = defaultdict(int)
        self.counts_above_threshold = None
        self.n_thresholds = len(self.thresholds)
        self.threshold_index = 0
        self.current_threshold = self.thresholds[self.threshold_index]
        self.current_count = 0

        self._i = 0
        self._is_done = False

        self.find_counts()
        self.counts_above_threshold = self.compute_complement()
        self.counter = NearestValueLookUp(self.counter)

    def advance_threshold(self):
        self.threshold_index += 1
        if self.threshold_index < self.n_thresholds:
            self.current_threshold = self.thresholds[self.threshold_index]
            self.counter[self.current_threshold] = self.current_count
            return True
        else:
            self._is_done = True
            return False

    def test(self, item):
        if item.score < self.current_threshold:
            self.current_count += 1
            self._i += 1
        else:
            # Rather than using recursion, just invert the condition
            # being tested and loop here.
            while self.advance_threshold():
                if item.score > self.current_threshold:
                    continue
                else:
                    self.current_count += 1
                    self._i += 1
                    break

    def find_counts(self):
        for item in self.series:
            self.test(item)

    def compute_complement(self):
        complement = defaultdict(int)
        n = len(self.series)

        for k, v in self.counter.items():
            complement[k] = n - v
        return NearestValueLookUp(complement)


# implementation derived from pyteomics
_precalc_fact = np.log([math.factorial(n) for n in range(20)])


def log_factorial(x):
    x = np.array(x)
    m = (x >= _precalc_fact.size)
    out = np.empty(x.shape)
    out[~m] = _precalc_fact[x[~m].astype(int)]
    x = x[m]
    out[m] = x * np.log(x) - x + 0.5 * np.log(2 * np.pi * x)
    return out


def _log_pi_r(d, k, p=0.5):
    return k * math.log(p) + log_factorial(k + d) - log_factorial(k) - log_factorial(d)


def _log_pi(d, k, p=0.5):
    return _log_pi_r(d, k, p) + (d + 1) * math.log(1 - p)


def _expectation(d, t, p=0.5):
    """The conditional tail probability for the negative binomial
    random variable for the number of incorrect target matches

    Parameters
    ----------
    d : int
        The number of decoys retained
    t : int
        The number of targets retained
    p : float, optional
        The parameter :math:`p` of the negative binomial,
        :math:`1 / 1 + (ratio of the target database to the decoy database)`

    Returns
    -------
    float
        The theoretical number of incorrect target matches

    References
    ----------
    Levitsky, L. I., Ivanov, M. V., Lobas, A. A., & Gorshkov, M. V. (2017).
    Unbiased False Discovery Rate Estimation for Shotgun Proteomics Based
    on the Target-Decoy Approach. Journal of Proteome Research, 16(2), 393–397.
    https://doi.org/10.1021/acs.jproteome.6b00144
    """
    if t is None:
        return d + 1
    t = int(t)
    m = np.arange(t + 1, dtype=int)
    pi = np.exp(_log_pi(d, m, p))
    return ((m * pi).cumsum() / pi.cumsum())[t]


def expectation_correction(targets, decoys, ratio):
    """Estimate a correction for the number of decoys at a given
    score threshold for small data size.

    Parameters
    ----------
    targets : int
        The number of targets retained
    decoys : int
        The number of decoys retained
    ratio : float
        The ratio of target database to decoy database

    Returns
    -------
    float
        The number of decoys to add for the correction

    References
    ----------
    Levitsky, L. I., Ivanov, M. V., Lobas, A. A., & Gorshkov, M. V. (2017).
    Unbiased False Discovery Rate Estimation for Shotgun Proteomics Based
    on the Target-Decoy Approach. Journal of Proteome Research, 16(2), 393–397.
    https://doi.org/10.1021/acs.jproteome.6b00144
    """
    p = 1. / (1. + ratio)
    tfalse = _expectation(decoys, targets, p)
    return tfalse


class TargetDecoyAnalyzer(object):
    """Estimate the False Discovery Rate using the Target-Decoy method.

    Attributes
    ----------
    database_ratio : float
        The ratio of the size of the target database to the decoy database
    target_weight : float
        A weight (less than 1.0) to put on target matches to make them weaker
        than decoys in situations where there is little data.
    decoy_correction : Number
        A quantity to use to correct for correcting for decoys, and if non-zero,
        will indicate that the negative binomial correction for decoys should be
        used.
    decoy_count : TYPE
        Description
    decoys : TYPE
        Description
    n_decoys_at : dict
        Description
    n_targets_at : dict
        Description
    target_count : TYPE
        Description
    targets : TYPE
        Description
    thresholds : TYPE
        Description
    with_pit : TYPE
        Description
    """

    def __init__(self, target_series, decoy_series, with_pit=False, decoy_correction=0, database_ratio=1.0,
                 target_weight=1.0):
        self.targets = target_series
        self.decoys = decoy_series
        self.target_count = len(target_series)
        self.decoy_count = len(decoy_series)
        self.database_ratio = database_ratio
        self.target_weight = target_weight
        self.with_pit = with_pit
        self.decoy_correction = decoy_correction
        self.calculate_thresholds()
        self._q_value_map = self._calculate_q_values()

    def calculate_thresholds(self):
        self.n_targets_at = {}
        self.n_decoys_at = {}

        target_series = self.targets
        decoy_series = self.decoys

        thresholds = sorted({case.score for case in target_series} | {case.score for case in decoy_series})
        self.thresholds = thresholds
        if len(thresholds) > 0:
            self.n_targets_at = ScoreThresholdCounter(
                target_series, self.thresholds).counts_above_threshold
            self.n_decoys_at = ScoreThresholdCounter(
                decoy_series, self.thresholds).counts_above_threshold

    def n_decoys_above_threshold(self, threshold):
        try:
            return self.n_decoys_at[threshold] + self.decoy_correction
        except IndexError:
            if len(self.n_decoys_at) == 0:
                return self.decoy_correction
            else:
                raise

    def n_targets_above_threshold(self, threshold):
        try:
            return self.n_targets_at[threshold]
        except IndexError:
            if len(self.n_targets_at) == 0:
                return 0
            else:
                raise

    def expectation_correction(self, t, d):
        return expectation_correction(t, d, self.database_ratio)

    def target_decoy_ratio(self, cutoff):

        decoys_at = self.n_decoys_above_threshold(cutoff)
        targets_at = self.n_targets_above_threshold(cutoff)
        decoy_correction = 0
        if self.decoy_correction:
            try:
                decoy_correction = self.expectation_correction(targets_at, decoys_at)
            except Exception as ex:
                print(ex)
        try:
            ratio = (decoys_at + decoy_correction) / float(
                targets_at * self.database_ratio * self.target_weight)
        except ZeroDivisionError:
            ratio = (decoys_at + decoy_correction)
        return ratio, targets_at, decoys_at

    def estimate_percent_incorrect_targets(self, cutoff):
        target_cut = self.target_count - self.n_targets_above_threshold(cutoff)
        decoy_cut = self.decoy_count - self.n_decoys_above_threshold(cutoff)
        percent_incorrect_targets = target_cut / float(decoy_cut)

        return percent_incorrect_targets

    def fdr_with_percent_incorrect_targets(self, cutoff):
        if self.with_pit:
            percent_incorrect_targets = self.estimate_percent_incorrect_targets(cutoff)
        else:
            percent_incorrect_targets = 1.0
        return percent_incorrect_targets * self.target_decoy_ratio(cutoff)[0]

    def _calculate_q_values(self):
        thresholds = sorted(self.thresholds, reverse=False)
        mapping = {}
        last_score = float('inf')
        last_q_value = 0
        for threshold in thresholds:
            try:
                q_value = self.fdr_with_percent_incorrect_targets(threshold)
                # If a worse score has a lower q-value than a better score, use that q-value
                # instead.
                if last_q_value < q_value and last_score < threshold:
                    q_value = last_q_value
                last_q_value = q_value
                last_score = threshold
                mapping[threshold] = q_value
            except ZeroDivisionError:
                mapping[threshold] = 1.
        return NearestValueLookUp(mapping)

    def q_values(self):
        q_map = self._q_value_map
        for target in self.targets:
            target.q_value = q_map[target.score]
        for decoy in self.decoys:
            decoy.q_value = q_map[decoy.score]

    def score(self, spectrum_match):
        spectrum_match.q_value = self._q_value_map[spectrum_match.score]
        return spectrum_match

    @property
    def q_value_map(self):
        return self._q_value_map


class GroupwiseTargetDecoyAnalyzer(object):
    def __init__(self, target_series, decoy_series, with_pit=False, grouping_functions=None, decoy_correction=0,
                 database_ratio=1.0, target_weight=1.0):
        if grouping_functions is None:
            grouping_functions = [lambda x: True]
        self.target_series = target_series
        self.decoy_series = decoy_series
        self.with_pit = with_pit
        self.grouping_functions = []
        self.groups = []
        self.group_fits = []
        self.decoy_correction = decoy_correction
        self.database_ratio = database_ratio
        self.target_weight = target_weight

        for fn in grouping_functions:
            self.add_group(fn)

        self.partition()

    def partition(self):
        for target in self.target_series:
            i = self.find_group(target)
            self.groups[i][0].append(target)
        for decoy in self.decoy_series:
            i = self.find_group(decoy)
            self.groups[i][1].append(decoy)
        for group in self.groups:
            fit = TargetDecoyAnalyzer(
                *group, with_pit=self.with_pit,
                decoy_correction=self.decoy_correction,
                database_ratio=self.database_ratio,
                target_weight=self.target_weight)
            self.group_fits.append(fit)

    def add_group(self, fn):
        self.grouping_functions.append(fn)
        self.groups.append(([], []))
        return len(self.groups)

    def find_group(self, spectrum_match):
        for i, fn in enumerate(self.grouping_functions):
            if fn(spectrum_match):
                return i
        return None

    def q_values(self):
        for group in self.group_fits:
            group.q_values()

    def score(self, spectrum_match):
        i = self.find_group(spectrum_match)
        fit = self.group_fits[i]
        return fit.score(spectrum_match)