#!/bin/bash

echo "=============================================="
echo "Testing Official OpenELIS Setup"
echo "=============================================="
echo ""

# Create a test directory
TEST_DIR="openelis-official-test"
mkdir -p $TEST_DIR
cd $TEST_DIR

echo "Step 1: Creating necessary directories and files..."
mkdir -p volume/database
mkdir -p volume/plugins
mkdir -p volume/analyzer
mkdir -p volume/properties
mkdir -p volume/odoo
mkdir -p volume/nginx
mkdir -p configuration

# Create .env file
cat > .env << 'EOF'
OE_DB_PASSWORD=changeme
ADMIN_PASSWORD=changeme
SSL_TRUSTSTORE_PATH=/etc/openelis-global/truststore
SSL_TRUSTSTORE_PASSWORD=tspass
SSL_KEYSTORE_PATH=/etc/openelis-global/keystore
SSL_KEYSTORE_PASSWORD=kspass
EOF

# Create database.env
cat > volume/database/database.env << 'EOF'
POSTGRES_DB=postgres
POSTGRES_USER=postgres
POSTGRES_PASSWORD=changeme
DB_PASSWORD=changeme
DB_SUPERUSER_PASSWORD=changeme
EOF

# Create minimal common.properties
cat > volume/properties/common.properties << 'EOF'
# OpenELIS Configuration
server.ssl.enabled=false
EOF

# Create minimal SystemConfiguration.properties
cat > volume/properties/SystemConfiguration.properties << 'EOF'
# System Configuration
EOF

# Create empty analyzer mapping
touch volume/analyzer/analyzer-test-map.csv

# Create empty odoo mapping  
touch volume/odoo/odoo-test-product-mapping.csv

# Create the docker-compose.yml
cat > docker-compose.yml << 'EOF'
version: '3.3'
services:
    certs:
        container_name: oe-certs
        image: itechuw/certgen:main
        platform: linux/amd64
        restart: "no"
        environment:
            - KEYSTORE_PW="kspass"
            - TRUSTSTORE_PW="tspass"
        networks:
            - default
        volumes:
            -  key_trust-store-volume:/etc/openelis-global
            -  keys-vol:/etc/ssl/private/
            -  certs-vol:/etc/ssl/certs/

    db.openelis.org:
        container_name: openelisglobal-database 
        image: itechuw/openelis-global-2-database:develop
        platform: linux/amd64
        ports:
            - "15432:5432"
        restart: always
        env_file:
            - ./volume/database/database.env
        environment:
            - DB_PASSWORD=${OE_DB_PASSWORD}
            - DB_SUPERUSER_PASSWORD=${ADMIN_PASSWORD} 
        volumes:
             - db-data:/var/lib/postgresql/data                
        networks:
            - default
        healthcheck:
            test: [ "CMD", "pg_isready", "-q", "-d", "clinlims", "-U", "clinlims" ]
            timeout: 45s
            interval: 10s
            retries: 10 
            
    oe.openelis.org:
        container_name: openelisglobal-webapp 
        image: itechuw/openelis-global-2:develop   
        platform: linux/amd64 
        depends_on:
            - db.openelis.org
            - certs
        ports:
            - "8080:8080"
            - "8443:8443"
        restart: always
        networks:
          default:
              ipv4_address: 172.20.1.121
        environment:
            - DEFAULT_PW=adminADMIN! 
            - TZ=Africa/Nairobi
            - CATALINA_OPTS= -Ddatasource.url=jdbc:postgresql://db.openelis.org:5432/clinlims -Ddatasource.username=clinlims -Ddatasource.password=${OE_DB_PASSWORD}
        volumes:
            -  key_trust-store-volume:/etc/openelis-global
            - ./volume/plugins/:/var/lib/openelis-global/plugins
            -  lucene_index-vol:/var/lib/lucene_index
            - ./volume/analyzer/analyzer-test-map.csv:/var/lib/openelis-global/analyzer/analyzer-test-map.csv
            - ./volume/properties/SystemConfiguration.properties:/var/lib/openelis-global/properties/SystemConfiguration.properties
            - ./volume/odoo/odoo-test-product-mapping.csv:/var/lib/openelis-global/odoo/odoo-test-product-mapping.csv
            - ./configuration:/var/lib/openelis-global/configuration
        secrets:
            - source: common.properties
            
    fhir.openelis.org:
        container_name: external-fhir-api
        image: itechuw/openelis-global-2-fhir:develop
        platform: linux/amd64
        depends_on:
            - db.openelis.org
            - certs
        ports:
            - "8081:8080"
            - "8444:8443"
        networks:
            - default
        restart: always
        environment:
          TZ: Africa/Nairobi
          FHIR_DATASOURCE_URL : "jdbc:postgresql://db.openelis.org:5432/clinlims?currentSchema=clinlims"
          FHIR_DATASOURCE_USERNAME: "clinlims"
          FHIR_DATASOURCE_PASSWORD: ${OE_DB_PASSWORD}
          FHIR_SERVER_ADRESS: "http://fhir.openelis.org:8080/fhir/"      
        volumes:
            -  key_trust-store-volume:/etc/openelis-global
        
    frontend.openelis.org:
        image: itechuw/openelis-global-2-frontend:develop
        container_name: openelisglobal-front-end
        platform: linux/amd64
        networks:
            - default
        environment:
            - CHOKIDAR_USEPOLLING=true
        tty: true

    proxy:
        image: itechuw/openelis-global-2-proxy:develop
        container_name: openelisglobal-proxy
        platform: linux/amd64
        ports:
            - 8888:80
            - 8889:443
        volumes:
            - certs-vol:/etc/nginx/certs/
            - keys-vol:/etc/nginx/keys/
        networks:
            - default
        restart: unless-stopped
        depends_on:
        - certs        
            
secrets:
  common.properties:
    file:  ./volume/properties/common.properties     

networks:
  default:
    driver: bridge
    ipam:
      config:
        - subnet: 172.20.1.0/24  
        
volumes:
  db-data:
  key_trust-store-volume:
  certs-vol:
  keys-vol:
  lucene_index-vol:
EOF

echo "✓ Files created"
echo ""

echo "Step 2: Starting containers..."
docker-compose up -d

echo ""
echo "Step 3: Waiting for services to start (this may take 3-5 minutes)..."
echo "Watching logs..."

# Monitor startup
docker-compose logs -f &
LOGS_PID=$!

sleep 180  # Wait 3 minutes

kill $LOGS_PID 2>/dev/null

echo ""
echo "Step 4: Testing the setup..."
echo ""

# Check proxy nginx config
echo "Checking proxy nginx configuration..."
docker exec openelisglobal-proxy cat /etc/nginx/nginx.conf > proxy-nginx.conf 2>/dev/null
if [ -f proxy-nginx.conf ]; then
    echo "✓ Proxy config retrieved"
    echo ""
    echo "=== PROXY NGINX CONFIG ==="
    cat proxy-nginx.conf
    echo "=========================="
    echo ""
fi

# Test HTTP endpoint
echo "Testing HTTP (port 8888)..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8888/ 2>/dev/null)
echo "HTTP Status: $HTTP_CODE"

# Test HTTPS endpoint
echo "Testing HTTPS (port 8889)..."
HTTPS_CODE=$(curl -k -s -o /dev/null -w "%{http_code}" https://localhost:8889/ 2>/dev/null)
echo "HTTPS Status: $HTTPS_CODE"

# Get HTML response
echo ""
echo "Getting HTML response..."
curl -k -s https://localhost:8889/ > response.html 2>/dev/null
echo "Response saved to response.html"

echo ""
echo "=============================================="
echo "Test Complete!"
echo "=============================================="
echo ""
echo "Access the application:"
echo "  HTTP:  http://localhost:8888/"
echo "  HTTPS: https://localhost:8889/"
echo ""
echo "Default credentials:"
echo "  Username: admin"
echo "  Password: adminADMIN!"
echo ""
echo "To view logs:"
echo "  docker-compose logs -f"
echo ""
echo "To stop:"
echo "  docker-compose down"
echo ""
echo "Check response.html to see what UI it's serving"
echo "=============================================="