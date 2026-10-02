# Reading the Hausie AI log

Hausie AI is designed to be observed before it is allowed to act. Start with
`auto_act: false`, `dry_run: true` and `log_level: info`.

| Log prefix | Meaning | Expected action |
| --- | --- | --- |
| `STARTUP` | App configuration was loaded. | Confirm the mode is `observe-and-suggest`. |
| `EVENT_STREAM connected` | Real-time Home Assistant event subscription is active. | No action required. |
| `SNAPSHOT` | Periodic state resync completed. | Check entity count is plausible. |
| `INVENTORY registry_sync` | Areas, devices, entities and labels were read locally from Home Assistant. | Confirm the counts look plausible after the first run. |
| `ENVIRONMENT` | An ambient or occupancy change was saved locally, with its normalized context band. | Confirm the entity and band look sensible. |
| `EVENT source=user` | Home Assistant identified a user-originated state change. | This may become training data if it is low-risk. |
| `LEARN` | A local observation was saved. | Verify the entity and context make sense. |
| `LEARN skipped` | Event was automation or unknown origin. | Expected default behaviour. |
| `DECISION` | The learner chose an outcome. | Review every `SUGGEST_ACTION` before enabling auto-act. |
| `FEEDBACK reward=-1` | A user reversed a recent Hausie AI action. | Investigate the pattern; keep auto-act disabled if needed. |
| `COLLECTOR failed` or `EVENT_STREAM disconnected` | Home Assistant API temporarily failed. | Check the app log and Home Assistant health. It reconnects automatically. |

`DO_NOTHING` is logged at `debug` level to keep the normal log readable. It is
a healthy, expected outcome whenever the model has insufficient evidence.

