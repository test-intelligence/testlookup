// ============================================================
// Jenkins pipeline — Integration tests (backend HTTP wiring)
// ============================================================
//
// "Integration" here matches the convention in
// ``backend/tests/integration/`` — these tests run real HTTP requests
// through the FastAPI app stack (router + middleware + DI + Pydantic
// validation) via ``httpx.ASGITransport``, but the DB layer is swapped
// for fakes via ``app.dependency_overrides``. No real Postgres / Redis
// required.
//
// This is the layer the unit tests skip: HTTP semantics, auth wiring,
// validation, routing. Slower than the unit pipeline (~5-8 min) because
// each test spins a full ASGI app.
//
// If you later add testcontainers-based tests that need real services,
// flip ``WITH_STACK=true`` to bring up docker compose first.
// ============================================================

pipeline {
  agent any

  options {
    timeout(time: 30, unit: 'MINUTES')
    timestamps()
    buildDiscarder(logRotator(numToKeepStr: '30'))
  }

  parameters {
    booleanParam(
      name: 'WITH_STACK',
      defaultValue: false,
      description: 'Bring up docker compose for any testcontainers-based or live-service tests. Off for the current in-process suite.',
    )
    string(
      name: 'PYTEST_K',
      defaultValue: '',
      description: 'Optional -k expression to scope the run (e.g. "auth or webhooks"). Empty = run everything under tests/integration/.',
    )
  }

  environment {
    DATABASE_URL    = 'postgresql+asyncpg://test:test@localhost:5432/test'
    MONGODB_URL     = 'mongodb://localhost:27017'
    REDIS_URL       = 'redis://localhost:6379/0'
    JWT_SECRET_KEY  = 'jenkins-integration-test-secret'
    STORAGE_BACKEND = 'local'
    AI_OFFLINE_MODE = 'true'
    APP_ENV         = 'test'
    PY_COLORS       = '0'
    FORCE_COLOR     = '0'
  }

  stages {
    stage('Checkout') {
      steps { checkout scm }
    }

    stage('Install deps') {
      steps {
        bat '''
          py -3.11 -m venv backend\\.venv
          call backend\\.venv\\Scripts\\activate.bat
          python -m pip install --upgrade pip wheel
          pip install -r backend\\requirements.txt
          pip install pytest pytest-asyncio httpx
        '''
      }
    }

    stage('Bring up stack') {
      // Only relevant for the testcontainers / live-DB case. The
      // ``when {}`` keeps the docker-compose ``up`` off the critical
      // path for normal in-process integration runs.
      when { expression { return params.WITH_STACK } }
      steps {
        bat 'docker compose up -d --build'
        // Wait for backend health before kicking off pytest. Without
        // this, tests start before alembic has finished migrating and
        // see "relation does not exist" 500s. 90s ceiling.
        retry(9) {
          bat '''
            curl --fail --silent --max-time 10 http://localhost:8000/health ^
              || (timeout /t 10 /nobreak >nul & exit /b 1)
          '''
        }
      }
    }

    stage('Run pytest (integration)') {
      steps {
        script {
          // Build the pytest command tail conditionally so an empty
          // -k expression doesn't add a stray quoted empty string
          // (which pytest treats as "select nothing", silently skipping
          // every test — a real CI gotcha).
          def kExpr = params.PYTEST_K?.trim()
          def kFlag = kExpr ? "-k \"${kExpr}\"" : ''

          bat """
            call backend\\.venv\\Scripts\\activate.bat
            cd backend
            pytest tests/integration ^
              --junitxml=test-results-integration.xml ^
              -q ^
              ${kFlag}
          """
        }
      }
      post {
        always {
          junit testResults: 'backend/test-results-integration.xml',
                allowEmptyResults: false,
                skipPublishingChecks: true
        }
      }
    }
  }

  post {
    always {
      // Tear down the stack regardless of pytest outcome so a failure
      // doesn't leave containers + volumes laying around between
      // builds. Idempotent — silent no-op when WITH_STACK was false.
      script {
        if (params.WITH_STACK) {
          // ``down -v`` clears volumes; the integration job is
          // self-contained so persisted DB state across runs is
          // a hazard, not a feature.
          bat 'docker compose down -v --remove-orphans || exit /b 0'
        }
      }
    }
  }
}
