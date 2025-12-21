#!/bin/bash

# Build script for multi-arch OpenELIS backend image
# Supports both AMD64 and ARM64 architectures
# Note: Build context is set to the parent directory (nidanhis) to access OpenELIS-Global-2

set -e

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Get the parent directory (nidanhis) as build context
BUILD_CONTEXT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DOCKERFILE="$SCRIPT_DIR/Dockerfile"

IMAGE_NAME="${IMAGE_NAME:-dipakthapa/openelis-global-2}"
IMAGE_TAG="${IMAGE_TAG:-latest}"

echo "Building multi-arch OpenELIS backend image: ${IMAGE_NAME}:${IMAGE_TAG}"
echo "Build context: ${BUILD_CONTEXT}"
echo "Dockerfile: ${DOCKERFILE}"

# Check if docker buildx is available
if ! docker buildx version > /dev/null 2>&1; then
    echo "Error: docker buildx is not available. Please install Docker Buildx."
    exit 1
fi

# Create and use a buildx builder if it doesn't exist
BUILDER_NAME="openelis-multiarch-builder"
if ! docker buildx inspect $BUILDER_NAME > /dev/null 2>&1; then
    echo "Creating buildx builder: $BUILDER_NAME"
    docker buildx create --name $BUILDER_NAME --use
else
    echo "Using existing buildx builder: $BUILDER_NAME"
    docker buildx use $BUILDER_NAME
fi

# Bootstrap the builder
docker buildx inspect --bootstrap

# Build for multiple platforms
echo "Building for linux/amd64 and linux/arm64..."
docker buildx build \
    --platform linux/amd64,linux/arm64 \
    --file "$DOCKERFILE" \
    --tag "${IMAGE_NAME}:${IMAGE_TAG}" \
    --push \
    "$BUILD_CONTEXT"

echo "Successfully built and pushed multi-arch image: ${IMAGE_NAME}:${IMAGE_TAG}"
echo "Platforms: linux/amd64, linux/arm64"

