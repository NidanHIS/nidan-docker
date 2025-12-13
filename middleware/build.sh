#!/bin/bash
# Build script to copy middleware JARs from source to docker build context

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIDDLEWARE_SOURCE="${SCRIPT_DIR}/../../nidan-middleware/integration"

echo "🔨 Building Nidan Middleware JARs..."
echo ""

# Check if source directory exists
if [ ! -d "$MIDDLEWARE_SOURCE" ]; then
    echo "❌ Error: Middleware source directory not found at: $MIDDLEWARE_SOURCE"
    echo "   Please ensure nidan-middleware/integration exists relative to this script"
    exit 1
fi

# Build the middleware
cd "$MIDDLEWARE_SOURCE"
echo "📦 Building Maven packages..."
mvn clean package -DskipTests -q

# Copy JARs to docker build context
echo ""
echo "📋 Copying JARs to docker build context..."

mkdir -p "${SCRIPT_DIR}/cis/target"
mkdir -p "${SCRIPT_DIR}/ois/target"

if [ -f "cis/target/nidan-cis-0.1.0-SNAPSHOT.jar" ]; then
    cp cis/target/nidan-cis-0.1.0-SNAPSHOT.jar "${SCRIPT_DIR}/cis/target/"
    echo "✅ Copied CIS JAR"
else
    echo "❌ Error: CIS JAR not found after build"
    exit 1
fi

if [ -f "ois/target/nidan-ois-0.1.0-SNAPSHOT.jar" ]; then
    cp ois/target/nidan-ois-0.1.0-SNAPSHOT.jar "${SCRIPT_DIR}/ois/target/"
    echo "✅ Copied OIS JAR"
else
    echo "❌ Error: OIS JAR not found after build"
    exit 1
fi

echo ""
echo "✅ Middleware JARs ready for Docker build!"
echo ""
echo "You can now build the Docker images:"
echo "  docker-compose build nidan-cis nidan-ois"
echo "  docker-compose up -d nidan-cis nidan-ois"

