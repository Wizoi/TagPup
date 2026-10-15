# Findings of a branch

Provisional numbers `B-n`: `tools/add_findings.py --take` gives each row its number in
docs/findings.md when the main session merges the branch, and removes this file.

| # | Found | Finding | Status |
|---|---|---|---|
| B-1 | 2026-10-15 | low: Views queue (#1362): the brief folded `tagpup/services/suggestions.py` into `tagpup/jobs/suggestions.py`, which tests/test_layers.py forbids (a job may not import the store); its three wrappers became `saved_in`, `keep` and `offer` of `tagpup/services/suggester.py` instead, beside the model the runs call. A decision made, not copied. | open |
| B-2 | 2026-10-15 | low: `tagpup/jobs/suggestions.py` `run` logs a Suggest refused by `Runtime.begin` (`OtherModelsInUse`, #761) with `logger.exception`, a traceback in the log for a refusal that is expected; the page shows the sentence. A job cannot import the runtime to tell it apart (tests/test_layers.py). | open |
| B-3 | 2026-10-15 | low: #761's refusal stops two runs on two model sets at once, but a server keeping its models (`keep_models`) still keeps the first set loaded after its run ends while a run of another library loads the second (`Runtime._release` drops a model only when no library uses its settings). The live libraries name one set (3 of 3, counted 2026-10-15), so no instance; whether `begin` should unload another set first is how long models are kept, the owner's. | open |
| B-4 | 2026-10-15 | low: `tagpup/services/suggester.py` keeps three stand-in arms outside this brief: `getattr(self.faces, "ready", None)` (the runtime's face model always has it), `getattr(self.embedder, "model_name"/"pretrained", "unknown")` and `getattr(self.suggester.index, "indexed", None)`; the test stand-ins would need `ready`, `model_name` and `indexed` to drop them. | open |
