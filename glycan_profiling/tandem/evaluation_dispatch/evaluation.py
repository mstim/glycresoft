from collections import defaultdict

from glycan_profiling.task import TaskBase

from ..spectrum_match import SpectrumMatch, MultiScoreSpectrumMatch, ScoreSet
from .task import TaskDeque


class SpectrumEvaluatorBase(object):

    def fetch_scan(self, key):
        return self.scan_map[key]

    def fetch_mass_shift(self, key):
        return self.mass_shift_map[key]

    def evaluate(self, scan, structure, *args, **kwargs):
        """Evaluate the match between ``structure`` and ``scan``

        Parameters
        ----------
        scan : ms_deisotope.ProcessedScan
            MSn scan to match against
        structure : object
            Structure to match against ``scan``
        *args
            Propagated to scoring function
        **kwargs
            Propagated to scoring function

        Returns
        -------
        SpectrumMatcherBase
        """
        raise NotImplementedError()

    def handle_instance(self, structure, scan, mass_shift):
        solution = self.evaluate(scan, structure, mass_shift=mass_shift,
                                 **self.evaluation_args)
        self.solution_map[scan.id, mass_shift.name] = self.solution_packer(solution)
        return solution

    def handle_item(self, structure, scan_specification):
        scan_specification = [(self.fetch_scan(i), self.fetch_mass_shift(j)) for i, j in scan_specification]
        solution_target = None
        solution = None
        for scan, mass_shift in scan_specification:
            solution = self.handle_instance(structure, scan, mass_shift)
            solution_target = solution.target
        if solution is not None:
            try:
                solution.target.clear_caches()
            except AttributeError:
                pass
        return self.pack_output(solution_target)

    def pack_output(self, target):
        raise NotImplementedError()


class LocalSpectrumEvaluator(SpectrumEvaluatorBase, TaskBase):
    def __init__(self, evaluator, scan_map, mass_shift_map, solution_packer, evaluation_args=None):
        self.evaluator = evaluator
        self.scan_map = scan_map
        self.mass_shift_map = mass_shift_map
        self.solution_packer = solution_packer
        self.solution_map = dict()
        self.evaluation_args = evaluation_args

    def evaluate(self, scan, structure, *args, **kwargs):
        return self.evaluator.evaluate(scan, structure, *args, **kwargs)

    def pack_output(self, target):
        package = (target, self.solution_map)
        self.solution_map = dict()
        return package

    def process(self, hit_map, hit_to_scan_map, scan_hit_type_map):
        deque_builder = TaskDeque()
        deque_builder.feed(hit_map, hit_to_scan_map, scan_hit_type_map)
        i = 0
        n = len(hit_to_scan_map)
        for target, scan_spec in deque_builder:
            i += 1
            if i % 1000 == 0:
                self.log("... %0.2f%% of Hits Searched (%d/%d)" %
                         (i * 100. / n, i, n))

            yield self.handle_item(target, scan_spec)


class SequentialIdentificationProcessor(TaskBase):
    def __init__(self, evaluator, mass_shift_map, evaluation_args=None, solution_handler_type=None):
        if evaluation_args is None:
            evaluation_args = dict()
        if solution_handler_type is None:
            solution_handler_type = SolutionHandler
        self.evaluation_method = evaluator
        self.evaluation_args = evaluation_args
        self.mass_shift_map = mass_shift_map
        self.solution_handler_type = solution_handler_type
        self.solution_handler = self.solution_handler_type({}, {}, self.mass_shift_map)
        self.structure_map = None
        self.scan_map = None

    def _make_evaluator(self, **kwargs):
        evaluator = LocalSpectrumEvaluator(
            self.evaluation_method,
            self.scan_map,
            self.mass_shift_map,
            self.solution_handler.packer,
            self.evaluation_args)
        return evaluator

    def process(self, scan_map, hit_map, hit_to_scan_map, scan_hit_type_map):
        self.structure_map = hit_map
        self.scan_map = self.solution_handler.scan_map = scan_map
        evaluator = self._make_evaluator()
        self.log("... Searching Hits (%d:%d)" % (
            len(hit_to_scan_map),
            sum(map(len, hit_to_scan_map.values())))
        )
        for target, score_map in evaluator.process(hit_map, hit_to_scan_map, scan_hit_type_map):
            self.store_result(target, score_map)
        self.log("Solutions Handled: %d" % (self.solution_handler.counter, ))
        return self.scan_solution_map

    @property
    def scan_solution_map(self):
        return self.solution_handler.scan_solution_map

    def store_result(self, target, score_map):
        """Save the spectrum match scores for ``target`` against the
        set of matched scans

        Parameters
        ----------
        target : object
            The structure that was matched
        score_map : dict
            Maps (scan id, mass shift name) to score
        """
        self.solution_handler(target, score_map)


class SolutionHandler(TaskBase):
    def __init__(self, scan_solution_map, scan_map, mass_shift_map, packer=None):
        if packer is None:
            packer = SolutionPacker()
        self.scan_solution_map = defaultdict(list, (scan_solution_map or {}))
        self.scan_map = scan_map
        self.mass_shift_map = mass_shift_map
        self.packer = packer
        self.counter = 0

    def _make_spectrum_match(self, scan_id, target, score, shift_type):
        return SpectrumMatch(
            self.scan_map[scan_id], target, score,
            mass_shift=self.mass_shift_map[shift_type])

    def store_result(self, target, score_map):
        """Save the spectrum match scores for ``target`` against the
        set of matched scans

        Parameters
        ----------
        target : object
            The structure that was matched
        score_map : dict
            Maps (scan id, mass shift name) to score
        """
        self.counter += 1
        j = 0
        for hit_spec, result_pack in score_map.items():
            scan_id, shift_type = hit_spec
            score = self.packer.unpack(result_pack)
            j += 1
            if j % 1000 == 0:
                self.log("...... Mapping match %d for %s on %s with score %r" % (j, target, scan_id, score))
            self.scan_solution_map[scan_id].append(
                self._make_spectrum_match(scan_id, target, score, shift_type))

    def __call__(self, target, score_map):
        return self.store_result(target, score_map)


class SolutionPacker(object):
    def __call__(self, spectrum_match):
        return spectrum_match.score

    def unpack(self, package):
        return package


class MultiScoreSolutionHandler(SolutionHandler):
    def __init__(self, scan_solution_map, scan_map, mass_shift_map, packer=None):
        if packer is None:
            packer = MultiScoreSolutionPacker()
        super(MultiScoreSolutionHandler, self).__init__(
            scan_solution_map, scan_map, mass_shift_map, packer)

    def _make_spectrum_match(self, scan_id, target, score, shift_type):
        return MultiScoreSpectrumMatch(
            self.scan_map[scan_id], target, score,
            mass_shift=self.mass_shift_map[shift_type])


class MultiScoreSolutionPacker(object):
    def __call__(self, spectrum_match):
        return ScoreSet.from_spectrum_matcher(spectrum_match).pack()

    def unpack(self, package):
        return ScoreSet.unpack(package)