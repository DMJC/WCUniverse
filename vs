#!/bin/bash
# This is a small wrapper that lets VegaStrike - Upon the Coldest Sea
# run from seemingly anywhere
set -e

VEGASTRIKE_LOCAL_SHARE_DIR="/home/james/Project/WCUniverse"
VEGASTRIKE_SHARE_DIR="/home/james/Project/WCUniverse"

#Let's keep a log of the console
if [ -f  ~/.wcuniverse/console.log ]; then 
  mv -f ~/.wcuniverse/console.log ~/.wcuniverse/console.bak
fi
if [ -f ~/.wcuniverse/console_err.log ]; then 
  mv -f ~/.wcuniverse/console_err.log ~/.wcuniverse/console_err.bak
fi

echo "Starting Vega Strike..."
echo ""

if [ -d "${VEGASTRIKE_LOCAL_SHARE_DIR}" ]; then
    vegastrike -d"${VEGASTRIKE_LOCAL_SHARE_DIR}" "$1"
elif [ -d "${VEGASTRIKE_SHARE_DIR}" ]; then
    vegastrike -d"${VEGASTRIKE_SHARE_DIR}" "$1"
else
    echo "Unknown Game Asset Data Location."
    echo "Do you have the game assets installed?"
    exit 1
fi
