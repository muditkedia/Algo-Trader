"""Evidence database: schema, models, connection management, and the logger.

The evidence store is the heart of the platform. Every signal any strategy
emits is recorded here - traded or not - together with its context, outcome,
and (when executed) the resulting trade. Confidence and strategy decisions are
learned from this accumulated evidence, never hand-tuned.
"""
