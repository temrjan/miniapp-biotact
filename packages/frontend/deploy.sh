#!/bin/bash
set -e

echo '🚀 Deploying mistore...'

cd /var/www/mistore

echo '📦 Installing dependencies...'
npm ci --silent

echo '🔨 Building...'
npm run build

echo '✅ Done! Site deployed at https://app.biotact.uz'
