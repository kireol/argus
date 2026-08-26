# Keep the BitBake recipe's files/ mirror in sync with the editable app/
# source. `app/` is what you edit and run locally; `files/` is a checked-in
# copy the recipe's SRC_URI (file://) points at -- BitBake's fetcher can't
# reach outside the layer, so the copy has to exist on disk (see the layer
# layout note in README.md).
#
# Run this after editing anything under app/:
#     make -C examples/yocto sync

RECIPE_FILES := meta-argus-demo/recipes-argus/argus-demo/files

.PHONY: sync
sync:
	cp app/argus_demo.py $(RECIPE_FILES)/argus_demo.py
	cp app/argus-demo.service $(RECIPE_FILES)/argus-demo.service
	@echo "Synced app/ -> $(RECIPE_FILES)/"
