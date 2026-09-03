.PHONY: bootstrap test app start stop status openwebui-fetch

bootstrap:
	git submodule update --init --recursive
	git -C open-webui remote get-url upstream >/dev/null 2>&1 || \
		git -C open-webui remote add upstream git@github.com:open-webui/open-webui.git

test:
	/bin/zsh tests/test_repository_layout.sh
	/bin/zsh runner/tests/test_stack.sh

app:
	/bin/zsh runner/build-app.sh

start:
	/bin/zsh runner/stack.sh start

stop:
	/bin/zsh runner/stack.sh stop

status:
	/bin/zsh runner/stack.sh status

openwebui-fetch:
	git -C open-webui fetch origin
	git -C open-webui fetch upstream

