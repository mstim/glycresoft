import logging

from ms_deisotope.peak_dependency_network.intervals import SpanningMixin

from glycan_profiling.structure.lru import LRUCache


logger = logging.getLogger("glycresoft.database.intervals")


class QueryIntervalBase(SpanningMixin):
    def __init__(self, center, start, end):
        self.center = center
        self.start = start
        self.end = end

    def copy(self):
        return QueryIntervalBase(self.center, self.start, self.end)

    def __hash__(self):
        return hash((self.start, self.center, self.end))

    def __eq__(self, other):
        return (self.start, self.center, self.end) == (other.start, other.center, other.end)

    def __repr__(self):
        return "QueryInterval(%0.4f, %0.4f)" % (self.start, self.end)

    def extend(self, other):
        self.start = min(self.start, other.start)
        self.end = max(self.end, other.end)
        self.center = (self.start + self.end) / 2.
        return self

    def scale(self, x):
        new = QueryIntervalBase(
            self.center,
            self.center - ((self.center - self.start) * x),
            self.center + ((self.end - self.center) * x))
        return new

    def difference(self, other):
        if self.start < other.start:
            if self.end < other.start:
                return self.copy()
            else:
                return QueryIntervalBase(self.center, self.start, other.start)
        elif self.start > other.end:
            return self.copy()
        elif self.start <= other.end:
            return QueryIntervalBase(self.center, other.end, self.end)
        else:
            return self.copy()


class PPMQueryInterval(QueryIntervalBase):

    def __init__(self, mass, error_tolerance=2e-5):
        self.center = mass
        self.start = mass - (mass * error_tolerance)
        self.end = mass + (mass * error_tolerance)


class FixedQueryInterval(QueryIntervalBase):

    def __init__(self, mass, width=3):
        self.center = mass
        self.start = mass - width
        self.end = mass + width


try:
    has_c = True
    _QueryIntervalBase = QueryIntervalBase
    _PPMQueryInterval = PPMQueryInterval
    _FixedQueryInterval = FixedQueryInterval

    from glycan_profiling._c.structure.intervals import (
        QueryIntervalBase, PPMQueryInterval, FixedQueryInterval)
except ImportError:
    has_c = False


class IntervalSet(object):

    def __init__(self, intervals=None):
        if intervals is None:
            intervals = list()
        self.intervals = sorted(intervals, key=lambda x: x.center)
        self._total_count = None
        self.compute_total_count()

    def _invalidate(self):
        self._total_count = None

    @property
    def total_count(self):
        if self._total_count is None:
            self.compute_total_count()
        return self._total_count

    def compute_total_count(self):
        self._total_count = 0
        for interval in self:
            self._total_count += interval.size
        return self._total_count

    def __iter__(self):
        return iter(self.intervals)

    def __len__(self):
        return len(self.intervals)

    def __getitem__(self, i):
        return self.intervals[i]

    def __repr__(self):
        return "%s(%r)" % (self.__class__.__name__, self.intervals)

    def _repr_pretty_(self, p, cycle):
        return p.pretty(self.intervals)

    def find_insertion_point(self, mass):
        lo = 0
        hi = len(self)
        if hi == 0:
            return 0, False
        while hi != lo:
            mid = (hi + lo) / 2
            x = self[mid]
            err = x.center - mass
            if abs(err) <= 1e-9:
                return mid, True
            elif (hi - lo) == 1:
                return mid, False
            elif err > 0:
                hi = mid
            else:
                lo = mid
        raise ValueError((hi, lo, err, len(self)))

    def extend_interval(self, target, expansion):
        logger.debug("Extending %r by %r", target, expansion)
        self._invalidate()
        target.extend(expansion)
        i, _ = self.find_insertion_point(target.center)
        consolidate = False
        if i > 0:
            before = self[i - 1]
            consolidate |= before.overlaps(target)
            # print(before, target)
        if i < len(self) - 1:
            after = self[i + 1]
            consolidate |= after.overlaps(target)
            # print(after, target)
        if consolidate:
            logger.debug("Consolidation was required for extension of %r by %r", target, expansion)
            self.consolidate()
            new_interval = self.find_interval(target)
            logger.debug("Consolidated interval %r spans the original %r", new_interval, target)
            return new_interval
        return target

    def insert_interval(self, interval):
        center = interval.center
        # if interval in self.intervals:
        #     raise ValueError("Duplicate Insertion")
        n = len(self)
        if n != 0:
            index, matched = self.find_insertion_point(center)
            index += 1
            if matched and self[index - 1].overlaps(interval):
                new_group = self.extend_interval(self[index - 1], interval)
                return new_group
            if index < n and interval.overlaps(self[index]):
                new_group = self.extend_interval(self[index], interval)
                return new_group
            if index == 1:
                if self[index - 1].center > center:
                    index -= 1
        else:
            index = 0
        self._insert_interval(index, interval)
        return interval

    def _insert_interval(self, index, interval):
        self._invalidate()
        self.intervals.insert(index, interval)

    def find_interval(self, query):
        lo = 0
        n = hi = len(self)
        while hi != lo:
            mid = (hi + lo) / 2
            x = self[mid]
            err = x.center - query.center
            if err == 0 or x.contains_interval(query):
                return x
            elif (hi - 1) == lo:
                best_err = abs(err)
                best_i = mid
                if mid < (n - 1):
                    err = abs(self[mid + 1].center - query.center)
                    if err < best_err:
                        best_err = err
                        best_i = mid + 1
                if mid > -1:
                    err = abs(self[mid - 1].center - query.center)
                    if err < best_err:
                        best_err = err
                        best_i = mid - 1
                return self[best_i]
            elif err > 0:
                hi = mid
            else:
                lo = mid

    def find(self, mass, ppm_error_tolerance):
        return self.find_interval(PPMQueryInterval(mass, ppm_error_tolerance))

    def remove_interval(self, center):
        self._invalidate()
        ix, match = self.find_insertion_point(center)
        self.intervals.pop(ix)

    def clear(self):
        self.intervals = []

    def consolidate(self):
        intervals = list(self)
        self.clear()
        if len(intervals) == 0:
            return
        result = []
        last = intervals[0]
        for current in intervals[1:]:
            if last.overlaps(current):
                last.extend(current)
            else:
                result.append(last)
                last = current
        result.append(last)
        for r in result:
            self.insert_interval(r)


class LRUIntervalSet(IntervalSet):

    def __init__(self, intervals=None, max_size=1000):
        super(LRUIntervalSet, self).__init__(intervals)
        self.max_size = max_size
        self.current_size = len(self)
        self.lru = LRUCache()
        for item in self:
            self.lru.add_node(item)

    def insert_interval(self, interval):
        if self.current_size == self.max_size:
            self.remove_lru_interval()
        super(LRUIntervalSet, self).insert_interval(interval)

    def _insert_interval(self, index, interval):
        super(LRUIntervalSet, self)._insert_interval(index, interval)
        self.lru.add_node(interval)
        self.current_size += 1

    def extend_interval(self, target, expansion):
        self.lru.remove_node(target)
        try:
            result = super(LRUIntervalSet, self).extend_interval(target, expansion)
            self.lru.add_node(result)
            return result
        except Exception:
            self.lru.add_node(target)
            raise

    def find_interval(self, query):
        match = super(LRUIntervalSet, self).find_interval(query)
        if match is not None:
            try:
                self.lru.hit_node(match)
            except KeyError:
                self.lru.add_node(match)
        return match

    def remove_lru_interval(self):
        lru_interval = self.lru.get_least_recently_used()
        logger.debug("Removing LRU interval %r", lru_interval)
        self.lru.remove_node(lru_interval)
        self.remove_interval(lru_interval.center)
        self.current_size -= 1

    def clear(self):
        super(LRUIntervalSet, self).clear()
        self.lru = LRUCache()
        self.current_size = len(self)

    def consolidate(self):
        super(LRUIntervalSet, self).consolidate()
        self.current_size = len(self)
