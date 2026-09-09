.PHONY: bootstrap test app start stop status openwebui-fetch \
	webui-enable webui-disable webui-restart webui-status

bootstrap:
	git submodule update --init --recursive
	git -C open-webui remote get-url upstream >/dev/null 2>&1 || \
		git -C open-webui remote add upstream git@github.com:open-webui/open-webui.git

test:
	/bin/zsh tests/test_repository_layout.sh
	/bin/sh tests/test-restore-teamclaude.sh
	node --test desktop-plugins/comp-count/plugin.test.mjs
	/bin/zsh runner/tests/test_stack.sh
	/bin/zsh runner/tests/test_launchd.sh

app:
	/bin/zsh runner/build-app.sh

start:
	/bin/zsh runner/stack.sh start

stop:
	/bin/zsh runner/stack.sh stop

status:
	/bin/zsh runner/stack.sh status

webui-enable:
	/bin/zsh runner/hermes-webui-launchd.sh enable

webui-disable:
	/bin/zsh runner/hermes-webui-launchd.sh disable

webui-restart:
	/bin/zsh runner/hermes-webui-launchd.sh restart

webui-status:
	/bin/zsh runner/hermes-webui-launchd.sh status

openwebui-fetch:
	git -C open-webui fetch origin
	git -C open-webui fetch upstream
