-- TestLookup — PostgreSQL initialization
-- Runs once when the container starts for the first time

-- Create the main application database (docker-compose creates this automatically,
-- but this ensures extensions are enabled)
\connect testlookup;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Confirm setup
SELECT 'Database initialized with extensions: uuid-ossp, pgcrypto, pg_trgm' AS status;
