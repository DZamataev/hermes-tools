.PHONY: bootstrap test app webui-enable webui-disable webui-restart webui-status

bootstrap:
	git submodule update --init --recursive

test:
	/bin/zsh tests/test_repository_layout.sh
	/bin/sh tests/test-restore-teamclaude.sh
	node --test desktop-plugins/comp-count/plugin.test.mjs
	/bin/zsh runner/tests/test_launchd.sh
	/bin/zsh runner/tests/test_webui_app.sh
	/bin/zsh runner/tests/test_app_bundle.sh

app:
	/bin/zsh runner/build-app.sh

webui-enable:
	/bin/zsh runner/hermes-webui-launchd.sh enable

webui-disable:
	/bin/zsh runner/hermes-webui-launchd.sh disable

webui-restart:
	/bin/zsh runner/hermes-webui-launchd.sh restart

webui-status:
	/bin/zsh runner/hermes-webui-launchd.sh status
