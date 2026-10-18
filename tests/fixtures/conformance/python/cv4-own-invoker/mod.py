class BaseService:
    def _execute_with_metrics(self, name, fn, *args):
        return fn(*args)


class ConflictService(BaseService):
    def _check_internal(self, reservation):
        pass

    def check_conflicts(self, reservation):
        return self._execute_with_metrics("check_conflicts", self._check_internal, reservation)
