import warnings
import struct

from collections import namedtuple

from glypy.utils import Enum, make_struct

from ms_deisotope import DeconvolutedPeakSet, isotopic_shift
from ms_deisotope.data_source.metadata import activation

from glycan_profiling.structure import (
    ScanWrapperBase)

from glycan_profiling.chromatogram_tree import Unmodified

from glycan_profiling.tandem.ref import TargetReference, SpectrumReference


neutron_offset = isotopic_shift()


class SpectrumMatchBase(ScanWrapperBase):
    __slots__ = ['scan', 'target', "_mass_shift"]

    def __init__(self, scan, target, mass_shift=None):
        if mass_shift is None:
            mass_shift = Unmodified
        self.scan = scan
        self.target = target
        self._mass_shift = None
        self.mass_shift = mass_shift

    @property
    def mass_shift(self):
        return self._mass_shift

    @mass_shift.setter
    def mass_shift(self, value):
        self._mass_shift = value

    @staticmethod
    def threshold_peaks(deconvoluted_peak_set, threshold_fn=lambda peak: True):
        deconvoluted_peak_set = DeconvolutedPeakSet([
            p for p in deconvoluted_peak_set
            if threshold_fn(p)
        ])
        deconvoluted_peak_set._reindex()
        return deconvoluted_peak_set

    def _theoretical_mass(self):
        return self.target.total_composition().mass

    def precursor_mass_accuracy(self, offset=0):
        observed = self.precursor_ion_mass
        theoretical = self._theoretical_mass() + (
            offset * neutron_offset) + self.mass_shift.mass
        return (observed - theoretical) / theoretical

    def determine_precursor_offset(self, probing_range=3):
        best_offset = 0
        best_error = float('inf')
        for i in range(probing_range + 1):
            error = abs(self.precursor_mass_accuracy(i))
            if error < best_error:
                best_error = error
                best_offset = i
        return best_offset

    def __reduce__(self):
        return self.__class__, (self.scan, self.target)

    def get_top_solutions(self):
        return [self]

    def __eq__(self, other):
        try:
            target_id = self.target.id
        except AttributeError:
            target_id = None
        try:
            other_target_id = self.target.id
        except AttributeError:
            other_target_id = None
        return (self.scan == other.scan) and (self.target == other.target) and (
            target_id == other_target_id)

    def __hash__(self):
        try:
            target_id = self.target.id
        except AttributeError:
            target_id = None
        return hash((self.scan.id, self.target, target_id))

    def is_hcd(self):
        activation_info = self.scan.activation
        if activation_info is None:
            if self.scan.ms_level == 1:
                return False
            else:
                warnings.warn("Activation information is missing. Assuming HCD")
                return True
        matched = activation_info.has_dissociation_type(activation.HCD) or\
            activation_info.has_dissociation_type(activation.CID) or\
            activation_info.has_dissociation_type(activation.UnknownDissociation)
        return matched

    def is_exd(self):
        activation_info = self.scan.activation
        if activation_info is None:
            if self.scan.ms_level == 1:
                return False
            else:
                warnings.warn("Activation information is missing. Assuming not ExD")
                return False
        matched = activation_info.has_dissociation_type(activation.ETD) or\
            activation_info.has_dissociation_type(activation.ECD)
        return matched


class SpectrumMatcherBase(SpectrumMatchBase):
    __slots__ = ["spectrum", "_score"]

    def __init__(self, scan, target, mass_shift=None):
        if mass_shift is None:
            mass_shift = Unmodified
        self.scan = scan
        self.spectrum = scan.deconvoluted_peak_set
        self.target = target
        self._score = 0
        self._mass_shift = None
        self.mass_shift = mass_shift

    @property
    def score(self):
        return self._score

    def match(self, *args, **kwargs):
        raise NotImplementedError()

    def calculate_score(self, *args, **kwargs):
        raise NotImplementedError()

    @classmethod
    def evaluate(cls, scan, target, *args, **kwargs):
        mass_shift = kwargs.pop("mass_shift", Unmodified)
        inst = cls(scan, target, mass_shift=mass_shift)
        inst.match(*args, **kwargs)
        inst.calculate_score(*args, **kwargs)
        return inst

    def __getstate__(self):
        return (self.score,)

    def __setstate__(self, state):
        self.score = state[0]

    def __reduce__(self):
        return self.__class__, (self.scan, self.target,)

    @staticmethod
    def load_peaks(scan):
        try:
            return scan.convert(fitted=False, deconvoluted=True)
        except AttributeError:
            return scan

    def __repr__(self):
        return "{self.__class__.__name__}({self.scan_id}, {self.spectrum}, {self.target}, {self.score})".format(
            self=self)

    def plot(self, ax=None, **kwargs):
        from glycan_profiling.plotting import spectral_annotation
        art = spectral_annotation.TidySpectrumMatchAnnotator(self, ax=ax)
        art.draw(**kwargs)
        return art


class DeconvolutingSpectrumMatcherBase(SpectrumMatcherBase):

    @staticmethod
    def load_peaks(scan):
        try:
            return scan.convert(fitted=True, deconvoluted=False)
        except AttributeError:
            return scan

    def __init__(self, scan, target):
        super(DeconvolutingSpectrumMatcherBase, self).__init__(scan, target)
        self.spectrum = scan.peak_set


class SpectrumMatch(SpectrumMatchBase):

    __slots__ = ['score', 'best_match', 'data_bundle', "q_value", 'id']

    def __init__(self, scan, target, score, best_match=False, data_bundle=None,
                 q_value=None, id=None, mass_shift=None):
        if data_bundle is None:
            data_bundle = dict()

        super(SpectrumMatch, self).__init__(scan, target, mass_shift)

        self.score = score
        self.best_match = best_match
        self.data_bundle = data_bundle
        self.q_value = q_value
        self.id = id

    def pack(self):
        return (self.target.id, self.score, int(self.best_match), self.mass_shift.name)

    @classmethod
    def unpack(cls, data, spectrum, resolver, offset=0):
        i = offset
        target_id = int(data[i])
        score = float(data[i + 1])
        try:
            best_match = bool(int(data[i + 2]))
        except ValueError:
            best_match = bool(data[i + 2])
        mass_shift_name = data[i + 3]
        mass_shift = resolver.resolve_mass_shift(mass_shift_name)
        match = SpectrumMatch(
            spectrum,
            resolver.resolve_target(target_id),
            score,
            best_match,
            mass_shift=mass_shift)
        i += 4
        return match, i

    def clear_caches(self):
        try:
            self.target.clear_caches()
        except AttributeError:
            pass

    def __reduce__(self):
        return self.__class__, (self.scan, self.target, self.score, self.best_match,
                                self.data_bundle, self.q_value, self.id, self.mass_shift)

    def evaluate(self, scorer_type, *args, **kwargs):
        if isinstance(self.scan, SpectrumReference):
            raise TypeError("Cannot evaluate a spectrum reference")
        elif isinstance(self.target, TargetReference):
            raise TypeError("Cannot evaluate a target reference")
        return scorer_type.evaluate(self.scan, self.target, *args, **kwargs)

    def __repr__(self):
        return "%s(%s, %s, %0.4f, %r)" % (
            self.__class__.__name__,
            self.scan, self.target, self.score, self.mass_shift)

    @classmethod
    def from_match_solution(cls, match):
        return cls(match.scan, match.target, match.score, mass_shift=match.mass_shift)

    def clone(self):
        return self.__class__(
            self.scan, self.target, self.score, self.best_match, self.data_bundle,
            self.q_value, self.id, self.mass_shift)


class ModelTreeNode(object):
    def __init__(self, model, children=None):
        if children is None:
            children = {}
        self.children = children
        self.model = model

    def get_model_node_for(self, scan, target, *args, **kwargs):
        for decider, model_node in self.children.items():
            if decider(scan, target, *args, **kwargs):
                return model_node.get_model_node_for(scan, target, *args, **kwargs)
        return self

    def evaluate(self, scan, target, *args, **kwargs):
        node = self.get_model_node_for(scan, target, *args, **kwargs)
        return node.model.evaluate(scan, target, *args, **kwargs)

    def __call__(self, scan, target, *args, **kwargs):
        node = self.get_model_node_for(scan, target, *args, **kwargs)
        return node.model(scan, target, *args, **kwargs)

    def load_peaks(self, scan):
        return self.model.load_peaks(scan)


class SpectrumMatchClassification(Enum):
    target_peptide_target_glycan = 0
    target_peptide_decoy_glycan = 1
    decoy_peptide_target_glycan = 2
    decoy_peptide_decoy_glycan = 3


_ScoreSet = make_struct("ScoreSet", ['glycopeptide_score', 'peptide_score', 'glycan_score'])


class ScoreSet(_ScoreSet):
    __slots__ = ()
    packer = struct.Struct("!fff")

    @classmethod
    def from_spectrum_matcher(cls, match):
        return cls(match.score, match.peptide_score(), match.glycan_score())

    def pack(self):
        return self.packer.pack(*self)

    @classmethod
    def unpack(cls, binary):
        return cls(*cls.packer.unpack(binary))


class FDRSet(make_struct("FDRSet", ['total_q_value', 'peptide_q_value', 'glycan_q_value', 'glycopeptide_q_value'])):
    __slots__ = ()
    packer = struct.Struct("!ffff")

    def pack(self):
        return self.packer.pack(*self)

    @classmethod
    def unpack(cls, binary):
        return cls(*cls.packer.unpack(binary))

    @classmethod
    def default(cls):
        return cls(1.0, 1.0, 1.0, 1.0)


class MultiScoreSpectrumMatch(SpectrumMatch):
    __slots__ = ('score_set', 'match_type', '_q_value_set')

    def __init__(self, scan, target, score_set, best_match=False, data_bundle=None,
                 q_value_set=None, id=None, mass_shift=None, match_type=None):
        if q_value_set is None:
            q_value_set = FDRSet.default()
        else:
            q_value_set = FDRSet(*q_value_set)
        self._q_value_set = None
        super(MultiScoreSpectrumMatch, self).__init__(
            scan, target, score_set[0], best_match, data_bundle, q_value_set[0],
            id, mass_shift)
        self.score_set = ScoreSet(*score_set)
        self.q_value_set = q_value_set
        self.match_type = SpectrumMatchClassification[match_type]

    @property
    def q_value_set(self):
        return self._q_value_set

    @q_value_set.setter
    def q_value_set(self, value):
        self._q_value_set = value
        self.q_value = self._q_value_set.total_q_value

    def __reduce__(self):
        return self.__class__, (self.scan, self.target, self.score_set, self.best_match,
                                self.data_bundle, self.q_value_set, self.id, self.mass_shift,
                                self.match_type.value)

    def pack(self):
        return (self.target.id, self.score_set.pack(), int(self.best_match),
                self.mass_shift.name, self.match_type.value)

    @classmethod
    def from_match_solution(cls, match):
        try:
            return cls(match.scan, match.target, ScoreSet.from_spectrum_matcher(match), mass_shift=match.mass_shift)
        except AttributeError:
            if isinstance(match, MultiScoreSpectrumMatch):
                return match
            else:
                raise
