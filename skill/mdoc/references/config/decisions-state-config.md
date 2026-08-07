# decisions.yaml and state.yaml

decisions.yaml stores durable user confirmations with IDs, topics, values, status, and timestamps. Do not ask again unless the underlying repository or requirement changed.

state.yaml stores phase and check progress only. It must not duplicate business rules or machine paths. Automation updates it after successful phase transitions; only explicit user acceptance sets accepted.

