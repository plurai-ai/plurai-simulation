"""The hook codec: one decoder and encoder per harness, one fail-posture wrapper shared by all.

Hook logic never sees a harness. ``hooks/run.py`` decodes the harness's stdin payload into an
``Event`` (``model``), calls the hook's ``handle(event) -> Decision``, and encodes the decision in
the one output shape that harness reads. A harness is a module here that defines
``decode(raw: bytes) -> Event`` and ``encode(decision, event) -> str``. Adding one also takes a row in
``codec.SHAPES`` and one in ``registry.CAPABILITIES``; ``codec.decode`` and ``codec.encode`` refuse a
harness that lacks either. The harness is always named by the generated hook command, never inferred from
the environment.
"""
