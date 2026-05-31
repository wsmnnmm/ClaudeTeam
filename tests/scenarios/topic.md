# Scenario: lightweight topic switching

Given a running ClaudeTeam deployment with a Feishu group connected

When the boss sends a message that starts with `#工作Bug`

Then `claudeteam topic current` shows `#工作Bug` as the current topic
And the manager pane receives a topic-context hint before the raw boss message
And if the same boss message has body text after the topic marker, the empty
topic receives a short initial capsule instead of showing "no capsule"

When the boss later sends a message without a leading `#`

Then the message continues the same current topic
And `claudeteam topic note <short fact>` appends only a short capsule note, not raw chat history
And the fast ack includes the topic line, for example `话题：延续 #工作Bug`

When there is no current topic and the boss sends an untagged message

Then the manager pane receives a triage hint to check `claudeteam topic list --all`
And the manager's next boss-visible reply states which topic it selected

When the manager creates or updates a task for that message

Then the task card uses `--topic <name>` so `claudeteam task list --topic <name>` shows it under that conversation lane

When the operator sends `/topic` in the Feishu group

Then the bot replies with a card listing the current topic, its capsule, and recent topics

When the operator sends `/topic show T-164` and only one topic clearly mentions `T-164`

Then the bot shows that existing topic instead of forcing the full topic name

When the operator sends `/topic switch TeamOps`

Then the current topic changes to `#TeamOps` without creating a task or waking unrelated workers

When the operator runs `claudeteam topic digest`

Then the output lists active topics with their one-line capsule previews and active linked tasks
And completed or cancelled tasks do not clutter the digest by default

When the operator runs `claudeteam topic digest --write reports/topic-digests`

Then the command writes `reports/topic-digests/topic-digest-YYYY-MM-DD.md`
And the watchdog can run the same write path when `[topic_digest].enabled = true`
without posting routine digest noise to the Feishu group
