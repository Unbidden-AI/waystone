BINARY := waystone
export CGO_ENABLED := 0

.PHONY: all deps vet build run cross release-snapshot clean

all: build

deps:
	go mod tidy

vet: deps
	go vet ./...

build: deps
	go build -o $(BINARY) .

run: build
	./$(BINARY) version

# Cross-compile static binaries for every target platform into ./dist
cross: deps
	@mkdir -p dist
	GOOS=darwin  GOARCH=amd64 go build -o dist/$(BINARY)-darwin-amd64 .
	GOOS=darwin  GOARCH=arm64 go build -o dist/$(BINARY)-darwin-arm64 .
	GOOS=linux   GOARCH=amd64 go build -o dist/$(BINARY)-linux-amd64 .
	GOOS=linux   GOARCH=arm64 go build -o dist/$(BINARY)-linux-arm64 .
	GOOS=windows GOARCH=amd64 go build -o dist/$(BINARY)-windows-amd64.exe .
	@echo "built:" && ls -1 dist

# Full archives + checksums for all platforms (requires goreleaser)
release-snapshot:
	goreleaser release --snapshot --clean

clean:
	rm -rf $(BINARY) dist
