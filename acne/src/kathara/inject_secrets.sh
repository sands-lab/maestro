#!/bin/bash

# ==============================================================================
# Script to dynamically inject environment variables into a Kathara lab.conf file.
# This version prioritizes variables from a local .env file.
# ==============================================================================

# File names
SOURCE_FILE="lab.conf.origin"
TARGET_FILE="lab.conf"
DOTENV_FILE=".env"

# --- Step 1: Read variables from local .env file if it exists ---
if [ -f "$DOTENV_FILE" ]; then
    echo "Found local environment file '$DOTENV_FILE'. Loading variables..."
    export $(grep -v '^#' "$DOTENV_FILE" | xargs)
fi

# --- Step 2: Check for the origin file and copy if it doesn't exist ---
if [ ! -f "$SOURCE_FILE" ]; then
    echo "WARNING: Original lab configuration file '$SOURCE_FILE' not found."
    echo "Cloning '$TARGET_FILE' to '$SOURCE_FILE'..."
    cp "$TARGET_FILE" "$SOURCE_FILE"
fi

# --- Step 3: Read the source file and write to the target file ---
echo "Processing '$SOURCE_FILE' and writing to '$TARGET_FILE'..."
> "$TARGET_FILE" # Clear the target file

while IFS= read -r line; do
    # Check if the line contains a shell variable to be replaced
    if [[ "$line" =~ \$.* ]]; then
    # Extract the variable name after the $ sign (e.g., GOOGLE_API_KEY)
    VAR_NAME=$(echo "$line" | sed -n 's/.*\$\([A-Z0-9_]*\).*/\1/p')

        # First, check the local .env file
        if [ -n "$(printenv "$local_env[$VAR_NAME]")" ]; then
            SWAPPED_LINE=$(echo "$line" | sed "s|\$$VAR_NAME|${local_env[$VAR_NAME]}|g")
            echo "$SWAPPED_LINE" >> "$TARGET_FILE"
            echo "SWAPPED (from .env):   $line"
            echo "TO:                    $SWAPPED_LINE"
        # If not in .env, check the system environment
        elif [ -n "$(printenv "$VAR_NAME")" ]; then
            SWAPPED_LINE=$(echo "$line" | sed "s|\$$VAR_NAME|${!VAR_NAME}|g")
            echo "$SWAPPED_LINE" >> "$TARGET_FILE"
            echo "SWAPPED (from system): $line"
            echo "TO:                    $SWAPPED_LINE"
        else
            # Variable not found, write the original line and log a warning
            echo "$line" >> "$TARGET_FILE"
            echo "WARNING: Variable '$VAR_NAME' not found in .env or system environment. Line left unchanged."
            echo "UNCHANGED: $line"
        fi
    else
        # No variables found, just copy the line as is
        echo "$line" >> "$TARGET_FILE"
    fi
done < "$SOURCE_FILE"

echo "Done. The final configuration is in '$TARGET_FILE'."
