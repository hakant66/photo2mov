APP = photo_to_mov
SRC = photo_to_mov.m

FRAMEWORKS = \
	-framework Foundation \
	-framework AVFoundation \
	-framework CoreImage \
	-framework CoreMedia \
	-framework CoreVideo \
	-framework CoreGraphics \
	-framework ImageIO

$(APP): $(SRC)
	clang -fobjc-arc $(SRC) -o $(APP) $(FRAMEWORKS)

.PHONY: clean run-web docker-build docker-run

run-web:
	python3 web_server.py

docker-build:
	docker build -t photo-to-mov-web .

docker-run:
	docker run --rm \
		--name photo-to-mov-container \
		-p 8000:8000 \
		-v "$(HOME):$(HOME)" \
		-e GENERATOR_BACKEND=ffmpeg \
		-e HOST=0.0.0.0 \
		-e PORT=8000 \
		-e DEFAULT_DURATION_SECONDS=5 \
		-e DEFAULT_START_SCALE=1.0 \
		-e DEFAULT_END_SCALE=1.11 \
		-e DEFAULT_OUTPUT_DIR="/Users/hakantaskin/Library/CloudStorage/GoogleDrive-taskin.baba@gmail.com/Other computers/TASKIN_LAPTOP/Etsy/E31T/Etsy" \
		photo-to-mov-web

clean:
	rm -f $(APP)
