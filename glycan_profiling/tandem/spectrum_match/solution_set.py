'''Represent collections of :class:`~SpectrumMatch` instances covering the same
spectrum, and methods for selecting which are worth keeping for downstream consideration.
'''
from .spectrum_match import SpectrumMatch, SpectrumReference, ScanWrapperBase, MultiScoreSpectrumMatch


class SpectrumMatchRetentionStrategyBase(object):
    """Encapsulate a method for filtering :class:`SpectrumMatch` objects
    out of a list according to a specific criterion.

    Attributes
    ----------
    threshold: object
        Some abstract threshold
    """
    def __init__(self, threshold):
        self.threshold = threshold

    def filter_matches(self, solution_set):
        """Filter :class:`SpectrumMatch` objects from a list

        Parameters
        ----------
        solution_set : list
            The list of :class:`SpectrumMatch` objects to filter.

        Returns
        -------
        list
        """
        raise NotImplementedError()

    def __call__(self, solution_set):
        return self.filter_matches(solution_set)

    def __repr__(self):
        return "{self.__class__.__name__}({self.threshold})".format(self=self)


class MinimumScoreRetentionStrategy(SpectrumMatchRetentionStrategyBase):
    """A strategy for filtering :class:`~.SpectrumMatch` from a list if
    their :attr:`~.SpectrumMatch.score` is less than :attr:`threshold`

    Parameters
    ----------
    solution_set : list
        The list of :class:`SpectrumMatch` objects to filter.

    Returns
    -------
    list
    """
    def filter_matches(self, solution_set):
        retain = []
        for match in solution_set:
            if match.score > self.threshold:
                retain.append(match)
        return retain


class MinimumMultiScoreRetentionStrategy(SpectrumMatchRetentionStrategyBase):
    """A strategy for filtering :class:`~.SpectrumMatch` from a list if
    their :attr:`~.SpectrumMatch.score_set` is less than :attr:`threshold`,
    assuming they share the same dimensions.

    Parameters
    ----------
    solution_set : list
        The list of :class:`SpectrumMatch` objects to filter.

    Returns
    -------
    list
    """
    def filter_matches(self, solution_set):
        retain = []
        for match in solution_set:
            for score_i, ref_i in zip(match.score_set, self.threshold):
                if score_i < ref_i:
                    break
            else:
                retain.append(match)
        return retain


class MaximumSolutionCountRetentionStrategy(SpectrumMatchRetentionStrategyBase):
    """A strategy for filtering :class:`~.SpectrumMatch` from a list to retain
    the top :attr:`threshold` entries.

    This assumes that `solution_set` is sorted.

    Parameters
    ----------
    solution_set : list
        The list of :class:`SpectrumMatch` objects to filter.

    Returns
    -------
    list
    """
    def group_solutions(self, solutions, threshold=1e-2):
        """Group solutions which have scores that are very close to one-another
        so that they are not arbitrarily truncated.

        Parameters
        ----------
        solutions : list
            A list of :class:`~.SpectrumMatchBase`
        threshold : float, optional
            The maxmimum distance between two scores to still be considered
            part of a group (the default is 1e-2)

        Returns
        -------
        list
        """
        groups = []
        if len(solutions) == 0:
            return groups
        current_group = [solutions[0]]
        last_solution = solutions[0]
        for solution in solutions[1:]:
            delta = abs(solution.score - last_solution.score)
            if delta > threshold:
                groups.append(current_group)
                current_group = [solution]
            else:
                current_group.append(solution)
            last_solution = solution
        groups.append(current_group)
        return groups

    def filter_matches(self, solution_set):
        groups = self.group_solutions(solution_set)
        return [b for a in groups[:self.threshold] for b in a]


class TopScoringSolutionsRetentionStrategy(SpectrumMatchRetentionStrategyBase):
    """A strategy for filtering :class:`~.SpectrumMatch` from a list to retain
    those with scores that are within :attr:`threshold` of the highest score in
    the set.

    This assumes that `solution_set` is sorted and that the highest score is at
    the 0th index..

    Parameters
    ----------
    solution_set : list
        The list of :class:`SpectrumMatch` objects to filter.

    Returns
    -------
    list
    """
    def filter_matches(self, solution_set):
        if len(solution_set) == 0:
            return solution_set
        best_score = solution_set[0].score
        retain = []
        for solution in solution_set:
            if (best_score - solution.score) < self.threshold:
                retain.append(solution)
        return retain


class QValueRetentionStrategy(SpectrumMatchRetentionStrategyBase):
    """A strategy for filtering :class:`~.SpectrumMatch` from a list to retain
    those with q-value that are less than :attr:`threshold`.

    Parameters
    ----------
    solution_set : list
        The list of :class:`SpectrumMatch` objects to filter.

    Returns
    -------
    list
    """
    def filter_matches(self, solution_set):
        retain = []
        for match in solution_set:
            if match.q_value < self.threshold:
                retain.append(match)
        return retain


class SpectrumMatchRetentionMethod(SpectrumMatchRetentionStrategyBase):
    """A collection of several :class:`SpectrumMatchRetentionStrategyBase`
    objects which are applied in order to iteratively filter out :class:`SpectrumMatch`
    objects.

    This class implements the same :class:`SpectrumMatchRetentionStrategyBase` API
    so it may be used interchangably with single strategies.

    Attributes
    ----------
    strategies: list
        The list of :class:`SpectrumMatchRetentionStrategyBase`
    """
    def __init__(self, strategies=None):  # pylint: disable=super-init-not-called
        if strategies is None:
            strategies = []
        self.strategies = strategies

    def filter_matches(self, solution_set):
        retained = list(solution_set)
        for strategy in self.strategies:
            retained = strategy(retained)
        return retained

    def __repr__(self):
        return "{self.__class__.__name__}({self.strategies!r})".format(self=self)


default_selection_method = SpectrumMatchRetentionMethod([
    MinimumScoreRetentionStrategy(4.),
    TopScoringSolutionsRetentionStrategy(3.),
    MaximumSolutionCountRetentionStrategy(100)
])


default_multiscore_selection_method = SpectrumMatchRetentionMethod([
    MinimumMultiScoreRetentionStrategy((1.0, 0., 0.)),
    TopScoringSolutionsRetentionStrategy(100.),
    MaximumSolutionCountRetentionStrategy(100),
])


class SpectrumSolutionSet(ScanWrapperBase):
    """A collection of spectrum matches against a single scan
    with different structures.

    Implements the :class:`Sequence` interface.

    Attributes
    ----------
    scan: :class:`~.Scan`-like
        The matched scan
    solutions: list
        The distinct spectrum matches.
    score: float
        The best match's score
    """
    spectrum_match_type = SpectrumMatch
    default_selection_method = default_selection_method

    def __init__(self, scan, solutions=None):
        if solutions is None:
            solutions = []
        self.scan = scan
        self.solutions = solutions
        self._is_sorted = False
        self._is_simplified = False
        self._is_top_only = False
        self._target_map = None
        self._q_value = None

    def is_multiscore(self):
        """Check whether this match has been produced by summarizing a multi-score
        match, rather than a single score match.

        Returns
        -------
        bool
        """
        return False

    def _invalidate(self):
        self._target_map = None
        self._is_sorted = False

    @property
    def score(self):
        """The best match's score

        Returns
        -------
        float
        """
        return self.best_solution().score

    @property
    def q_value(self):
        """The best match's q-value

        Returns
        -------
        float
        """
        if self._q_value is None:
            self._q_value = self.best_solution().q_value
        return self._q_value

    @q_value.setter
    def q_value(self, value):
        self._q_value = value

    def _make_target_map(self):
        self._target_map = {
            sol.target: sol for sol in self
        }

    def solution_for(self, target):
        """Find the spectrum match from this set which corresponds
        to the provided `target` structure

        Parameters
        ----------
        target : object
            The target to search for

        Returns
        -------
        :class:`~.SpectrumMatchBase`
        """
        if self._target_map is None:
            self._make_target_map()
        return self._target_map[target]

    def precursor_mass_accuracy(self):
        """The precursor mass accuracy of the best match

        Returns
        -------
        float
        """
        return self.best_solution().precursor_mass_accuracy()

    def best_solution(self):
        """The :class:`SpectrumMatchBase` in :attr:`solutions` which
        is the best match to :attr:`scan`, the match at position 0.

        If the collection is not sorted, :meth:`sort` will be called.

        Returns
        -------
        :class:`~.SpectrumMatchBase`
        """
        if not self._is_sorted:
            self.sort()
        return self.solutions[0]

    def __repr__(self):
        if len(self) == 0:
            return "SpectrumSolutionSet(%s, [])" % (self.scan,)
        return "SpectrumSolutionSet(%s, %s, %f)" % (
            self.scan, self.best_solution().target, self.best_solution().score)

    def __getitem__(self, i):
        return self.solutions[i]

    def __iter__(self):
        return iter(self.solutions)

    def __len__(self):
        return len(self.solutions)

    def simplify(self):
        """Discard excess information in this collection to save
        space.

        Converts :attr:`scan` to a :class:`~.SpectrumReference`, and
        converts all matches to :class:`~.SpectrumMatch`, discarding
        matcher-specific information.

        """
        if self._is_simplified:
            return
        self.scan = SpectrumReference(
            self.scan.id, self.scan.precursor_information)
        solutions = []
        if len(self) > 0:
            best_score = self.best_solution().score
            for sol in self.solutions:
                sm = self.spectrum_match_type.from_match_solution(sol)
                if abs(sm.score - best_score) < 1e-6:
                    sm.best_match = True
                sm.scan = self.scan
                solutions.append(sm)
            self.solutions = solutions
        self._is_simplified = True
        self._invalidate()

    def get_top_solutions(self, d=3):
        """Get all matches within `d` of the best solution

        Parameters
        ----------
        d : float, optional
            The delta between the best match and the worst to return
            (the default is 3)

        Returns
        -------
        list
        """
        best_score = self.best_solution().score
        return [x for x in self.solutions if (best_score - x.score) < d]

    def select_top(self, method=None):
        """Filter spectrum matches in this collection in-place.

        If all the solutions would be filtered out, only the best solution
        will be kept.

        Parameters
        ----------
        method : :class:`SpectrumRetentionStrategyBase`, optional
            The filtering strategy to use, a callable object that returns a
            shortened list of :class:`~.SpectrumMatchBase` instances.
            If :const:`None`, :attr:`default_selection_method` will be used.

        """
        if method is None:
            method = self.default_selection_method
        if self._is_top_only:
            return
        if not self._is_sorted:
            self.sort()
        if len(self) > 0:
            best_solution = self.best_solution()
            after = method(self)
            self.solutions = after
            if len(self) == 0:
                self.solutions = [best_solution]
        self._is_top_only = True
        self._invalidate()
        return self

    def sort(self, maximize=True):
        """Sort the spectrum matches in this solution set according to their score
        attribute.

        In the event of a tie, in order to enforce determistic behavior, this will also
        sort matches according to their target's id attribute.

        Sets :attr:`_is_sorted` to :const:`True`.

        Parameters
        ----------
        maximize : bool, optional
            If true, sort descending order instead of ascending. Defaults to :const:`True`

        See Also
        --------
        sort_by
        sort_q_value
        """
        self.solutions.sort(key=lambda x: (x.score, x.target.id), reverse=maximize)
        self._is_sorted = True
        return self

    def sort_by(self, sort_fn=None, maximize=True):
        """Sort the spectrum matches in this solution set according to `sort_fn`.

        This method behaves the same way :meth:`sort` does, except instead of
        sorting on an intrinsic attribute it uses a callable. It uses the same
        determistic augmentation as :meth:`sort`

        Parameters
        ----------
        sort_fn : Callable, optional
            The sort key function to use. If not provided, falls back to :meth:`sort`.
        maximize : bool, optional
            If true, sort descending order instead of ascending. Defaults to :const:`True`

        See Also
        --------
        sort
        """
        if sort_fn is None:
            return self.sort(maximize=maximize)
        self.solutions.sort(key=lambda x: (sort_fn(x), x.target.id), reverse=maximize)
        self._is_sorted = True
        return self

    def sort_q_value(self):
        """Sort the spectrum matches in this solution set according to their q_value
        attribute.

        In the event of a tie, in order to enforce determistic behavior, this will also
        sort matches according to their target's id attribute.

        Sets :attr:`_is_sorted` to :const:`True`.

        See Also
        --------
        sort
        sort_by
        """
        self.solutions.sort(key=lambda x: (x.q_value, x.target.id), reverse=False)
        self._is_sorted = True
        return self

    def merge(self, other):
        self._invalidate()
        self.solutions.extend(other)
        self.sort()
        if self._is_top_only:
            self._is_top_only = False
            self.select_top()
        return self

    def threshold(self, method=None):
        return self.select_top(method)

    def clone(self):
        dup = self.__class__(self.scan, [
            s.clone() for s in self.solutions
        ])
        dup._is_simplified = self._is_simplified
        dup._is_top_only = self._is_top_only
        dup._is_sorted = self._is_sorted
        return dup

    def __eq__(self, other):
        if self.scan.id != other.scan.id:
            return False
        return self.solutions == other.solutions

    def __ne__(self, other):
        return not (self == other)


class MultiScoreSpectrumSolutionSet(SpectrumSolutionSet):
    spectrum_match_type = MultiScoreSpectrumMatch
    default_selection_method = default_multiscore_selection_method

    def is_multiscore(self):
        return True

    # note: Sorting by total score is not guaranteed to sort by total
    # FDR, so a post-FDR estimation re-ranking of spectrum matches will
    # be necessary.

    def sort(self, maximize=True):
        """Sort the spectrum matches in this solution set according to their score_set
        attribute.

        In the event of a tie, in order to enforce determistic behavior, this will also
        sort matches according to their target's id attribute.

        Sets :attr:`_is_sorted` to :const:`True`.

        See Also
        --------
        sort_by
        sort_q_value
        """
        self.solutions.sort(key=lambda x: (
            x.score_set, x.target.id), reverse=maximize)
        self._is_sorted = True
        return self

    def sort_q_value(self):
        """Sort the spectrum matches in this solution set according to their q_value_set
        attribute.

        In the event of a tie, in order to enforce determistic behavior, this will also
        sort matches according to their target's id attribute.

        Sets :attr:`_is_sorted` to :const:`True`.

        See Also
        --------
        sort
        sort_by
        """
        self.solutions.sort(key=lambda x: (
            x.q_value_set, x.score_set, x.target.id), reverse=False)
        self._is_sorted = True
        return self

    @property
    def score_set(self):
        """The best match's score set

        Returns
        -------
        ScoreSet
        """
        return self.best_solution().score_set

    @property
    def q_value_set(self):
        """The best match's q-value set

        Returns
        -------
        FDRSet
        """
        return self.best_solution().q_value_set
