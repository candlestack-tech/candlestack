"""Worker: runs the models users uploaded, one job at a time, in a container without network
(``python -m candlestack.worker``; docs/architecture.md, "Worker").

``service`` is the loop: it takes a job from the queue of ``candlestack.jobs``, runs it in a
process of its own (``runner``) with a time limit, and publishes its result. Only that process
imports Keras (``model``: the safe load of an uploaded file); it builds the model's input with
``candlestack.preprocessing``, from the job's candles or, for the trial run of a ``check``, the
built-in ones (``samples``).

Nothing imports this package: the API reaches it through the job queue. It is empty on
purpose, so starting the job's process imports nothing of the loop and the loop nothing of
Keras.
"""
