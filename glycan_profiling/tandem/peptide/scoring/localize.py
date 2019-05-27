from collections import namedtuple, defaultdict
import itertools

import numpy as np
from scipy.special import comb
from decimal import Decimal
import math

from glycopeptidepy.utils.memoize import memoize
from ms_deisotope.utils import Base
from ms_deisotope.peak_set import window_peak_set
from glycopeptidepy.algorithm import PeptidoformGenerator


@memoize(100000000000)
def binomial_pmf(n, i, p):
    try:
        return comb(n, i, exact=True) * (p ** i) * ((1 - p) ** (n - i))
    except OverflowError:
        dn = Decimal(n)
        di = Decimal(i)
        dp = Decimal(p)
        x = math.factorial(dn) / (math.factorial(di) * math.factorial(dn - di))
        return float(x * dp ** di * ((1 - dp) ** (dn - di)))


class PeakWindow(object):
    def __init__(self, peaks):
        self.peaks = list(peaks)
        self.max_mass = 0
        self._calculate()

    def __iter__(self):
        return iter(self.peaks)

    def __getitem__(self, i):
        return self.peaks[i]

    def __len__(self):
        return len(self.peaks)

    def _calculate(self):
        self.peaks.sort(key=lambda x: x.intensity, reverse=True)
        self.max_mass = 0
        for peak in self.peaks:
            if peak.neutral_mass > self.max_mass:
                self.max_mass = peak.neutral_mass

    def __repr__(self):
        template = "{self.__class__.__name__}({self.max_mass}, {size})"
        return template.format(self=self, size=len(self))


ProbableSitePair = namedtuple("ProbableSitePair", ['peptide1', 'peptide2', 'modifications', 'peak_depth'])
ModificationAssignment = namedtuple("ModificationAssignment", ["site", "modification"])


class AScoreCandidate(object):
    def __init__(self, peptide, modifications, fragments=None):
        self.peptide = peptide
        self.modifications = modifications
        self.fragments = fragments

    def __hash__(self):
        return hash(self.peptide)

    def __eq__(self, other):
        return self.peptide == other.peptide and self.modifications == other.modifications

    def make_solution(self, a_score, permutations=None):
        return AScoreSolution(self.peptide, a_score, self.modifications, permutations, self.fragments)

    def __repr__(self):
        template = "{self.__class__.__name__}({d})"

        def formatvalue(v):
            if isinstance(v, float):
                return "%0.4f" % v
            else:
                return str(v)
        d = [
            "%s=%s" % (k, formatvalue(v)) if v is not self else "(...)" for k, v in sorted(
                self.__dict__.items(), key=lambda x: x[0])
            if (not k.startswith("_") and not callable(v))
            and not (v is None) and k != "fragments"]

        return template.format(self=self, d=', '.join(d))


class AScoreSolution(AScoreCandidate):
    def __init__(self, peptide, a_score, modifications, permutations, fragments=None):
        super(AScoreSolution, self).__init__(peptide, modifications, fragments)
        self.a_score = a_score
        self.permutations = permutations


class AScoreEvaluator(object):
    '''
    Calculate a localization statistic for given peptidoform and modification rule.

    The original probabilistic model is described in [1]. Implementation based heavily
    on the OpenMS implementation [2].

    References
    ----------
    [1] Beausoleil, S. a, Villén, J., Gerber, S. a, Rush, J., & Gygi, S. P. (2006).
        A probability-based approach for high-throughput protein phosphorylation analysis
        and site localization. Nature Biotechnology, 24(10), 1285–1292. https://doi.org/10.1038/nbt1240
    [2] Rost, H. L., Sachsenberg, T., Aiche, S., Bielow, C., Weisser, H., Aicheler, F., … Kohlbacher, O. (2016).
        OpenMS: a flexible open-source software platform for mass spectrometry data analysis. Nat Meth, 13(9),
        741–748. https://doi.org/10.1038/nmeth.3959
    '''
    def __init__(self, scan, peptide, modification_rule, n_positions=1):
        self._scan = None
        self.peak_windows = None
        self.scan = scan
        self.peptide = peptide
        self.modification_rule = modification_rule
        self.n_positions = n_positions
        self.peptidoforms = self.generate_peptidoforms(self.modification_rule)
        self._fragment_cache = {}

    @property
    def scan(self):
        return self._scan

    @scan.setter
    def scan(self, value):
        self._scan = value
        if value is None:
            self.peak_windows = []
        else:
            self.peak_windows = map(PeakWindow, window_peak_set(value.deconvoluted_peak_set))

    def find_existing(self, modification_rule):
        '''Find existing modifications derived from this rule

        Parameters
        ----------
        modification_rule: :class:`~.ModificationRule`
            The modification rule to search for

        Returns
        -------
        indices: list
            The indices of :attr:`peptide` where modifications were found
        '''
        indices = []
        for i, position in enumerate(self.peptide):
            if modification_rule in position.modifications:
                indices.append(i)
        return indices

    def generate_base_peptides(self, modification_rule):
        existing_indices = self.find_existing(modification_rule)
        base_peptides = []
        for indices in itertools.combinations(existing_indices, self.n_positions):
            base_peptide = self.peptide.clone()
            for i in indices:
                base_peptide.drop_modification(i, modification_rule)
            base_peptides.append(base_peptide)
        return base_peptides

    def generate_peptidoforms(self, modification_rule):
        base_peptides = self.generate_base_peptides(modification_rule)
        pepgen = PeptidoformGenerator([], [modification_rule], self.n_positions)
        peptidoforms = defaultdict(set)
        for base_peptide in base_peptides:
            mod_combos = pepgen.modification_sites(base_peptide)
            for mod_combo in mod_combos:
                if len(mod_combo) != self.n_positions:
                    continue
                mod_combo = [ModificationAssignment(*mc) for mc in mod_combo]
                peptidoform, n_mods = pepgen.apply_variable_modifications(
                    base_peptide, mod_combo, None, None)
                peptidoforms[peptidoform].update(tuple(mod_combo))
        return [AScoreCandidate(peptide, sorted(mods), self._generate_fragments(peptide))
                for peptide, mods in  peptidoforms.items()]

    def _generate_fragments(self, peptidoform):
        frags = itertools.chain.from_iterable(
            itertools.chain(
                peptidoform.get_fragments("y"),
                peptidoform.get_fragments("b")))
        frags = list(frags)
        frags.sort(key=lambda x: x.mass)
        return frags

    def match_ions(self, fragments, depth=10, error_tolerance=1e-5):
        '''Match fragments against the windowed peak set at a given
        peak depth.

        Parameters
        ----------
        fragments: list
            A list of peptide fragments, sorted by mass
        depth: int
            The peak depth to search to, the `i`th most intense peak in
            each window
        error_tolerance: float
            The PPM error tolerance to use when matching peaks.

        Returns
        -------
        int:
            The number of fragments matched
        '''
        n = 0
        window_i = 0
        window_n = len(self.peak_windows)
        current_window = self.peak_windows[window_i]
        for frag in fragments:
            while not current_window or (frag.mass >= (current_window.max_mass + 1)) :
                window_i += 1
                if window_i == window_n:
                    return n
                current_window = self.peak_windows[window_i]
            for peak in current_window[:depth]:
                if abs(peak.neutral_mass - frag.mass) / frag.mass < error_tolerance:
                    n += 1
        return n

    def permutation_score(self, peptidoform):
        '''Calculate the binomial statistic for this peptidoform
        using the top 1 to 10 peaks.

        Parameters
        ----------
        peptidoform: :class:`~.PeptideSequence`
            The peptidoform to score

        Returns
        -------
        :class:`numpy.ndarray`:
            The binomial score at peak depth `i + 1`

        See Also
        --------
        :meth:`_score_at_window_depth`
        :meth:`match_ions`
        '''
        fragments = peptidoform.fragments
        N = len(fragments)
        site_scores = np.zeros(10)
        for i in range(1, 11):
            site_scores[i - 1] = self._score_at_window_depth(fragments, N, i)
        return site_scores

    def _score_at_window_depth(self, fragments, N, i):
        '''Score a fragment collection at a given peak depth, and
        calculate the binomial score based upon the probability mass
        function.

        Parameters
        ----------
        fragments: list
            A list of peptide fragments, sorted by mass
        N: int
            The maximum number of theoretical fragments
        i: int
            The peak depth to search through

        Returns
        -------
        float
        '''
        n = self.match_ions(fragments, i)
        p = i / 100.0
        cumulative_score = binomial_pmf(N, n, p)
        return (abs(-10.0 * math.log10(cumulative_score)))

    def rank_permutations(self, permutation_scores):
        ranking = []
        for i in range(len(permutation_scores)):
            weighted_score = self._weighted_score(permutation_scores[i])
            ranking.append((weighted_score, i))
        ranking.sort(reverse=True)
        return ranking

    # Taken directly from reference [1]
    _weight_vector = np.array([
        0.5, 0.75, 1.0, 1.0, 1.0, 1.0, 0.75, 0.5, .25, .25
    ])

    def _weighted_score(self, scores):
        return self._weight_vector.dot(scores) / 10.0

    def score(self, error_tolerance=1e-5):
        scores = [self.permutation_score(candidate) for candidate in self.peptidoforms]
        ranked = self.rank_permutations(scores)
        solutions = [self.peptidoforms[i].make_solution(score, scores[i])
                     for score, i in ranked]
        delta_scores = []
        pairs = self.find_highest_scoring_permutations(solutions)
        peptide = solutions[0]
        for pair in pairs:
            delta_score = self.calculate_delta(pair)
            pair.peptide1.a_score = delta_score
            delta_scores.append((pair.modifications, delta_score))
        peptide.a_score = delta_scores
        return peptide

    def find_highest_scoring_permutations(self, solutions):
        sites = []
        best_solution = solutions[0]
        site_assignments_for_best_solution = best_solution.modifications
        permutation_pairs = []
        # for each modification under permutation, find the next best solution which
        # does not have this modification in its set of permuted modifications, and
        # package the pair into a :class:`ProbableSitePair`.
        for site in best_solution.modifications:
            for alt_solution in solutions[1:]:
                if site not in alt_solution.modifications:
                    peak_depth = np.argmax(best_solution.permutations - alt_solution.permutations) + 1
                    permutation_pairs.append(ProbableSitePair(best_solution, alt_solution, site, peak_depth))
                    break
        return permutation_pairs

    def site_determining_ions(self, solutions):
        frag_sets = [set(sol.fragments) for sol in solutions]
        common = set.intersection(*frag_sets)
        n = len(solutions)
        site_determining = []
        for i, solution in enumerate(solutions):
            cur_frags = frag_sets[i]
            if i == n - 1:
                diff = cur_frags - common
                site_determining.append(sorted(diff, key=lambda x: x.mass))
            else:
                diff = cur_frags - common - frag_sets[i + 1]
                site_determining.append(sorted(diff, key=lambda x: x.mass))
        return site_determining

    def calculate_delta(self, candidate_pair):
        if candidate_pair.peptide1 == candidate_pair.peptide2:
            return 0.0
        site_frags = self.site_determining_ions(
            [candidate_pair.peptide1, candidate_pair.peptide2])
        site_frags1, site_frags2 = site_frags[0], site_frags[1]
        N1 = len(site_frags1)
        N2 = len(site_frags2)
        peak_depth = candidate_pair.peak_depth
        P1 = self._score_at_window_depth(site_frags1, N1, peak_depth)
        P2 = self._score_at_window_depth(site_frags2, N2, peak_depth)
        return P1 - P2
