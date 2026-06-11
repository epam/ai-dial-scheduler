# Common rule
`run_id`,`schedule_id` and `result`/`exception` are used in schedule_run lifecycle management.

# Successful jobs
If jub successful it should return standardized object with next fields:

- `run_id`: uuid4
- `schedule_id`: str
- `result`: json # accumulated list of values in case of streamed chat completion or sse, or direct response json

# Failed jobs
Scheduled job execution wrapped in `try/except` block and reraises standardized exception
that includes additional fields:

- `run_id`: uuid4
- `schedule_id`: str
