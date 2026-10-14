# S3C memory channel policy (§8)

```
initial_plan_retrievable = success only
```

| channel | stored | auditable | initial plan |
|---|---|---|---|
| success | yes | yes | **yes** |
| failure | yes | yes | **no** (`inactive_until_S4`) |
| manual_review | yes | yes | no |
| rejected | yes | yes | no |

S3C does not implement activation logic.
