import json
import warnings

from collections import defaultdict
from io import StringIO

import numpy as np

from .base import (
    UniformCountScoringModelBase,
    DecayRateCountScoringModelBase,
    LogarithmicCountScoringModelBase,
    MassScalingCountScoringModel,
    ScoringFeatureBase)


class ChargeStateDistributionScoringModelBase(ScoringFeatureBase):
    feature_type = "charge_count"

    def get_state_count(self, chromatogram):
        return chromatogram.n_charge_states

    def get_states(self, chromatogram):
        return chromatogram.charge_states

    def get_signal_proportions(self, chromatogram):
        proportions = {}
        states = self.get_states(chromatogram)
        rest = chromatogram
        total = 0
        for state in states:
            part, rest = chromatogram.bisect_charge(state)
            proportions[state] = part.total_signal
            total += part.total_signal
        for k in proportions:
            proportions[k] /= total
        return proportions


_CHARGE_MODEL = ChargeStateDistributionScoringModelBase


class UniformChargeStateScoringModel(
        _CHARGE_MODEL, UniformCountScoringModelBase):
    pass


class DecayRateChargeStateScoringModel(
        _CHARGE_MODEL, DecayRateCountScoringModelBase):
    pass


class LogarithmicChargeStateScoringModel(
        _CHARGE_MODEL, LogarithmicCountScoringModelBase):
    pass


def decay(x, step=0.4, rate=1.5):
    v = 0
    for i in range(x):
        v += (step / (i + rate))
    return v


def ones(x):
    return (x - (np.floor(x / 10.) * 10))


def neighborhood_of(x, scale=100.):
    n = x / scale
    up = ones(n) > 5
    if up:
        neighborhood = (np.floor(n / 10.) + 1) * 10
    else:
        neighborhood = (np.floor(n / 10.) + 1) * 10
    return neighborhood * scale


uniform_model = UniformChargeStateScoringModel()
decay_model = DecayRateChargeStateScoringModel()


class MassScalingChargeStateScoringModel(_CHARGE_MODEL, MassScalingCountScoringModel):
    def handle_missing_neighborhood(self, chromatogram, neighborhood, *args, **kwargs):
        warnings.warn(
            ("%f was not found for this charge state "
             "scoring model. Defaulting to uniform model") % neighborhood)
        return uniform_model.score(chromatogram, *args, **kwargs)

    def handle_missing_bin(self, chromatogram, bins, key, neighborhood, *args, **kwargs):
        warnings.warn("%d not found for this mass range (%f). Using bin average (%r)" % (
            key, neighborhood, chromatogram.charge_states))
        return sum(bins.values()) / float(len(bins))

    def transform_state(self, state):
        return abs(state)

    @classmethod
    def fit(cls, observations, missing=0.01, neighborhood_width=100.,
            ignore_singly_charged=False):
        bins = defaultdict(lambda: defaultdict(float))

        self = cls({}, neighborhood_width=neighborhood_width)

        for sol in observations:
            neighborhood = self.neighborhood_of(sol.neutral_mass)
            for c in sol.charge_states:
                if ignore_singly_charged and abs(c) == 1:
                    continue
                bins[neighborhood][c] += 1

        model_table = {}

        all_states = set()
        for level in bins.values():
            all_states.update(level.keys())

        all_states.add(1 * (min(all_states) / abs(min(all_states))))

        for neighborhood, counts in bins.items():
            for c in all_states:
                counts[c] += missing
            total = sum(counts.values())
            entry = {k: v / total for k, v in counts.items()}
            model_table[neighborhood] = entry

        return cls(model_table, neighborhood_width)

    def save(self, file_obj):
        json.dump(
            {"neighborhood_width": self.neighborhood_width, "table": self.table},
            file_obj, indent=4, sort_keys=True)

    @classmethod
    def load(cls, file_obj):
        data = json.load(file_obj)
        table = data.pop("table")
        width = float(data.pop("neighborhood_width"))

        def numeric_keys(table, dtype=float, convert_value=lambda x: x):
            return {abs(dtype(k)): convert_value(v) for k, v in table.items()}

        table = numeric_keys(table, convert_value=lambda x: numeric_keys(x, int))

        return cls(table=table, neighborhood_width=width)

    def clone(self):
        text_buffer = StringIO()
        self.save(text_buffer)
        text_buffer.seek(0)
        return self.load(text_buffer)


class WeightedMassScalingChargeStateScoringModel(MassScalingChargeStateScoringModel):
    @classmethod
    def fit(cls, observations, missing=0.01, neighborhood_width=100.,
            ignore_singly_charged=False, smooth=0):
        bins = defaultdict(lambda: defaultdict(float))

        self = cls({}, neighborhood_width=neighborhood_width)

        for sol in observations:
            neighborhood = self.neighborhood_of(sol.neutral_mass)
            for c in sol.charge_states:
                if ignore_singly_charged and abs(c) == 1:
                    continue
                component, _ = sol.bisect_charge(c)
                bins[neighborhood][c] += component.total_signal / sol.total_signal

        model_table = {}

        all_states = set()
        for level in bins.values():
            all_states.update(level.keys())

        all_states.add(1 * (min(all_states) / abs(min(all_states))))

        for neighborhood, counts in bins.items():
            largest_charge = None
            largest_charge_total = 0
            for c in all_states:
                counts[c] += missing
                if counts[c] > largest_charge_total:
                    largest_charge = c
                    largest_charge_total = counts[c]
            if smooth > 0:
                smooth_shift = largest_charge_total * smooth
                for c in all_states:
                    if c != largest_charge and counts[c] > missing:
                        counts[c] += smooth_shift

            total = sum(counts.values())
            entry = {k: v / total for k, v in counts.items()}
            model_table[neighborhood] = entry

        return cls(model_table, neighborhood_width)
