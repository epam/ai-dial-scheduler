from src.lib.exceptions.errors import ScheduleRunError


class TestScheduleRunError:
    def test_status_code_defaults_to_none(self):
        err = ScheduleRunError("boom", schedule_id="s1", run_id="r1")
        assert err.status_code is None
        assert err.schedule_id == "s1"
        assert err.run_id == "r1"

    def test_status_code_is_stored(self):
        err = ScheduleRunError("boom", schedule_id="s1", run_id="r1", status_code=502)
        assert err.status_code == 502
