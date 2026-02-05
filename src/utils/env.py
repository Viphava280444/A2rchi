import os


def read_secret(secret_name, default=""):
    """
    Read a secret from a file or environment variable.
    
    Args:
        secret_name: Name of the secret (e.g., 'POSTGRES_PASSWORD')
        default: Default value if secret is not found
        
    Returns:
        The secret value, or the default if not found
    """
    # fetch filepath from env variable
    secret_filepath = os.getenv(f"{secret_name}_FILE")

    if secret_filepath:
        # read secret from file and return
        with open(secret_filepath, 'r') as f:
            secret = f.read()
        return secret.strip()

    # fallback to direct environment variable if no *_FILE is set
    env_value = os.getenv(secret_name)
    if env_value:
        return env_value.strip()

    return default