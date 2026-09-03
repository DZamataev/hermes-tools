.PHONY: bootstrap local-env install-plugin test bridge-stack-test contract-test app start stop status openwebui-fetch

bootstrap:
	git submodule update --init --recursive
	git -C open-webui remote get-url upstream >/dev/null 2>&1 || \
		git -C open-webui remote add upstream git@github.com:open-webui/open-webui.git
	$(MAKE) local-env

local-env:
	/bin/zsh runner/bootstrap-local-env.sh

install-plugin:
	/bin/zsh hermes-plugin/scripts/install.sh

test:
	/bin/zsh tests/test_repository_layout.sh
	/bin/zsh runner/tests/test_stack.sh

bridge-stack-test:
	/bin/zsh tests/test_bridge_stack.sh

contract-test:
	cd bridge-service && uv run --python 3.12 --extra test pytest ../tests/contract/test_openwebui_live.py -q -rs

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
