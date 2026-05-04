# Operator targets for the test_a (and similar) Docker deployments.
#
# Boss-flagged 2026-05-04: deployments must never fail because the
# container's claude OAuth token expired. The host's macOS keychain
# always holds a fresh refreshable token; we extract it onto the
# bind-mounted file every time we redeploy.
#
# Usage:
#   make deploy   # rebuild image + refresh creds + recreate + up
#   make creds    # just refresh creds (no rebuild) — token expired,
#                 # need to bounce panes
#   make smoke    # send /help /team /health /usage /tmux to chat for
#                 # eyeball verification
#
# Requires: macOS host (uses `security`), docker, npx (for lark-cli).

.PHONY: creds build deploy reset up smoke down

CHAT ?= oc_989e33567a4be168c7e7a286287a3965
PROFILE ?= test-live-a

creds:
	@echo "→ Refreshing claude OAuth from keychain (host-side)…"
	@mkdir -p $(HOME)/.claude
	@security find-generic-password -s "Claude Code-credentials" -w \
		> $(HOME)/.claude/.credentials.json
	@python3 bin/show_cred_expiry.py $(HOME)/.claude/.credentials.json

build:
	docker compose build claudeteam

deploy: creds build
	docker compose down
	docker compose up -d
	docker compose exec -T claudeteam claudeteam reset --yes
	docker compose exec -T claudeteam claudeteam up
	@echo "✅ deploy done · check /health from chat"

reset:
	docker compose exec -T claudeteam claudeteam reset --yes
	docker compose exec -T claudeteam claudeteam up

down:
	docker compose down

smoke:
	@for cmd in "/help" "/team" "/health" "/usage" "/tmux manager 12"; do \
		echo "→ $$cmd"; \
		npx -y @larksuite/cli im +messages-send \
			--chat-id $(CHAT) \
			--text "$$cmd" --as user --profile $(PROFILE) > /dev/null 2>&1; \
		sleep 5; \
	done
	@echo "✅ sent 5 commands · scroll the chat to verify cards"
