# Packages the Argus Demo channel as a sideloadable .zip.
#
#   make zip     -> build/channel.zip (manifest at the zip root)
#   make clean   -> remove build/

.PHONY: zip clean

zip:
	mkdir -p build
	rm -f build/channel.zip
	zip -r build/channel.zip manifest source components images -x '*.DS_Store'

clean:
	rm -rf build
