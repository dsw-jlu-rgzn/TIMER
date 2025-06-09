#!/bin/bash
#
# SLURM configuration (modify as needed for your compute environment)
#SBATCH --job-name=llm_judge
#SBATCH --mem=100G     
#SBATCH --time=2:30:00
#SBATCH --ntasks=1

# Set up conda environment
# Modify CONDA_DIR to point to your conda installation
CONDA_DIR="${HOME}/miniconda3"
export CONDA_ENVS_DIRS="${CONDA_DIR}/envs"
export CONDA_PKGS_DIRS="${CONDA_DIR}/pkgs"
source ${CONDA_DIR}/etc/profile.d/conda.sh

which conda
conda info --envs
conda config --show
conda activate timer-env  # Change to your environment name

# Base directory and API settings
export OPENAI_API_BASE=${OPENAI_API_BASE:-"https://api.openai.com/v1"}
export OPENAI_API_KEY=${OPENAI_API_KEY:-"your-api-key-here"}
DATE=${DATE:-$(date +%Y%m%d)}

# Set up directories (modify these paths as needed)
BASE_DIR="${HOME}/TIMER"
RESULT_DIR="${BASE_DIR}/result/${DATE}"
mkdir -p "${RESULT_DIR}"

# Array of subdirectories to process
SUBDIRS=(
    # Add model output directories as needed
)

# Process each subdirectory
cd "${BASE_DIR}/evaluate/llm_judge"
for SUBDIR in "${SUBDIRS[@]}"
do
    OUTPUT_JSON_FILE="${RESULT_DIR}/${SUBDIR}_judge_output.json"
    
    echo "Processing: ${SUBDIR}"
    echo "Output will be saved to: ${OUTPUT_JSON_FILE}"
    
    # Run the evaluation script
    python llm_as_judge.py \
        --model_generated_responses "${BASE_DIR}/result/final_generations/${SUBDIR}.csv" \
        --output_file "${OUTPUT_JSON_FILE}" \
        --reference_answers "${BASE_DIR}/result/clinician-instruction-responses.csv" \
        --ehr_dir "../data/MedAlign/full_patient_ehrs" \
        --context_length 16384
    
    echo "Completed processing ${SUBDIR}"
    echo "----------------------------------------"
done

echo "All evaluations completed."
