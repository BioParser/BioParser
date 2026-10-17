"""Long-running job processes.

The parse worker lives in `worker.parse`. The extractor lives in `worker.extract`.
Claim and failure handling are shared in `worker.stage`. Loading a job is `worker.job`.
Each stage records its own result.
"""
