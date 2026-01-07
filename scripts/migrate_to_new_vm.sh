#!/bin/bash
#
# Full VM Migration Script
# Copies everything needed to run on a new VM
#
# Usage:
#   1. Create new VM in GCP
#   2. Set NEW_VM_IP below
#   3. Run: ./scripts/migrate_to_new_vm.sh
#

set -e

# ============================================
# CONFIGURE THIS
# ============================================
NEW_VM_IP="${1:-}"
NEW_VM_USER="${2:-theo}"

if [ -z "$NEW_VM_IP" ]; then
    echo "Usage: $0 <NEW_VM_IP> [username]"
    echo "Example: $0 34.76.123.45 theo"
    exit 1
fi

echo "============================================"
echo "POLYMARKET ML - FULL VM MIGRATION"
echo "============================================"
echo "Target: $NEW_VM_USER@$NEW_VM_IP"
echo ""

# ============================================
# STEP 1: Create database dump
# ============================================
echo "[1/6] Creating database dump..."
cd ~/polymarket-ml
docker-compose exec -T postgres pg_dump -U postgres -Fc polymarket_ml > /tmp/polymarket_backup.dump
echo "      Database dump: $(du -h /tmp/polymarket_backup.dump | cut -f1)"

# ============================================
# STEP 2: Create config archive
# ============================================
echo "[2/6] Creating config archive..."
tar -czvf /tmp/polymarket_configs.tar.gz \
    -C ~/polymarket-ml \
    .env \
    config.yaml \
    strategies.yaml \
    gcp-credentials.json \
    .export_state.json \
    2>/dev/null || true
echo "      Config archive: $(du -h /tmp/polymarket_configs.tar.gz | cut -f1)"

# ============================================
# STEP 3: Create Claude config archive
# ============================================
echo "[3/6] Creating Claude config archive..."
tar -czvf /tmp/claude_config.tar.gz \
    -C ~ \
    .claude \
    .claude.json \
    2>/dev/null || true
echo "      Claude archive: $(du -h /tmp/claude_config.tar.gz | cut -f1)"

# ============================================
# STEP 4: Export crontab
# ============================================
echo "[4/6] Exporting crontab..."
crontab -l > /tmp/crontab_backup.txt 2>/dev/null || echo "# No crontab" > /tmp/crontab_backup.txt
echo "      Crontab exported"

# ============================================
# STEP 5: Copy files to new VM
# ============================================
echo "[5/6] Copying files to new VM..."
echo "      This may take a while for the database dump..."

# Copy database dump (largest file)
scp /tmp/polymarket_backup.dump ${NEW_VM_USER}@${NEW_VM_IP}:~/

# Copy config archives
scp /tmp/polymarket_configs.tar.gz ${NEW_VM_USER}@${NEW_VM_IP}:~/
scp /tmp/claude_config.tar.gz ${NEW_VM_USER}@${NEW_VM_IP}:~/
scp /tmp/crontab_backup.txt ${NEW_VM_USER}@${NEW_VM_IP}:~/

echo "      Files copied successfully"

# ============================================
# STEP 6: Generate setup script for new VM
# ============================================
echo "[6/6] Generating setup script..."

cat > /tmp/setup_new_vm.sh << 'SETUP_SCRIPT'
#!/bin/bash
#
# Run this on the NEW VM after files are copied
#

set -e

echo "============================================"
echo "SETTING UP POLYMARKET ML ON NEW VM"
echo "============================================"

# Install Docker
echo "[1/8] Installing Docker..."
if ! command -v docker &> /dev/null; then
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker $USER
    echo "      Docker installed. You may need to logout/login for group changes."
fi

# Install docker-compose
echo "[2/8] Installing docker-compose..."
if ! command -v docker-compose &> /dev/null; then
    sudo apt update && sudo apt install -y docker-compose
fi

# Clone repo
echo "[3/8] Cloning repository..."
if [ ! -d ~/polymarket-ml ]; then
    git clone https://github.com/YOUR_REPO/polymarket-ml.git ~/polymarket-ml
fi
cd ~/polymarket-ml

# Extract configs
echo "[4/8] Extracting configs..."
tar -xzvf ~/polymarket_configs.tar.gz -C ~/polymarket-ml/

# Extract Claude config
echo "[5/8] Extracting Claude config..."
tar -xzvf ~/claude_config.tar.gz -C ~/

# Start postgres
echo "[6/8] Starting PostgreSQL..."
docker-compose up -d postgres redis
sleep 10

# Restore database
echo "[7/8] Restoring database (this may take a while)..."
docker-compose exec -T postgres pg_restore -U postgres -d polymarket_ml --clean --if-exists < ~/polymarket_backup.dump || true

# Start all services
echo "[8/8] Starting all services..."
docker-compose up -d

# Restore crontab
echo "[+] Restoring crontab..."
crontab ~/crontab_backup.txt

echo ""
echo "============================================"
echo "SETUP COMPLETE!"
echo "============================================"
echo ""
echo "Verify with:"
echo "  docker-compose ps"
echo "  curl http://localhost:8000/api/monitoring/health"
echo ""
echo "Access dashboard at: http://$(curl -s ifconfig.me)"
SETUP_SCRIPT

scp /tmp/setup_new_vm.sh ${NEW_VM_USER}@${NEW_VM_IP}:~/
ssh ${NEW_VM_USER}@${NEW_VM_IP} "chmod +x ~/setup_new_vm.sh"

echo ""
echo "============================================"
echo "MIGRATION FILES COPIED!"
echo "============================================"
echo ""
echo "Files on new VM:"
echo "  ~/polymarket_backup.dump     - Database (restore this)"
echo "  ~/polymarket_configs.tar.gz  - App configs"
echo "  ~/claude_config.tar.gz       - Claude credentials"
echo "  ~/crontab_backup.txt         - Cron jobs"
echo "  ~/setup_new_vm.sh            - Setup script"
echo ""
echo "Next steps on NEW VM:"
echo "  1. SSH to new VM: ssh ${NEW_VM_USER}@${NEW_VM_IP}"
echo "  2. Run setup:     ./setup_new_vm.sh"
echo ""
echo "Note: Update the git repo URL in setup_new_vm.sh if needed"
