"""Dataset hub: published event logs, copied into a user's workspace on use.

Layout:

* ``licenses`` - the curated license list (and which ones count as "open").
* ``gate`` - takedown (410) and external-AI egress checks on *derived* logs. No
  service imports, so ``auth`` / the module loader can import it.
* ``access`` - who may curate / read a dataset.
* ``storage`` - the only writer under ``data/datasets/``.
* ``stats`` - revision stats + preview, through ``EventLogAccess`` only.
* ``service`` - publish / update / use / disable / enable / purge / list.

See ``docs/strategy/dataset-hub-plan.md``. Keep this ``__init__`` import-free:
``mate.api.auth.ownership`` imports ``hub.gate``.
"""
