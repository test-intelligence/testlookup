// ============================================================
// Jenkins pipeline — Unit tests (backend + frontend)
// ============================================================
//
// Pure unit tests: no docker compose stack, no live databases.
// The backend tests stub external services via conftest fixtures;
// the env vars below satisfy ``app.core.config.Settings`` at
// import time so module-level engine bootstraps don't ArgumentError
// (see memory ``project_test_env``).
//
// Runs the two test trees in parallel — they share nothing.
// ============================================================

pipeline {
  agent any

  options {
    timeout(time: 20, unit: 'MINUTES')
    timestamps()
    buildDiscarder(logRotator(numToKeepStr: '50'))
    // Tests are read-only; concurrent builds against different branches
    // are fine. Don't disable concurrency here.
  }

  // Trigger guidance: wire this to multibranch-pipeline scan-on-push,
  // or attach a webhook via ``Generic Webhook Trigger``. Manual run
  // via "Build Now" is also supported.
  //
  // triggers {
  //   pollSCM('H/5 * * * *')
  // }

  environment {
    // Module-import requirements for `app.db.postgres`, `app.db.mongo`,
    // etc. Values don't have to point at live services — backend unit
    // tests stub the clients. The URLs only need to parse.
    DATABASE_URL    = 'postgresql+asyncpg://test:test@localhost:5432/test'
    MONGODB_URL     = 'mongodb://localhost:27017'
    REDIS_URL       = 'redis://localhost:6379/0'
    JWT_SECRET_KEY  = 'jenkins-unit-test-secret'
    STORAGE_BACKEND = 'local'
    // Avoid CI surprises from optional integrations that probe the
    // network at import time.
    AI_OFFLINE_MODE = 'true'
    APP_ENV         = 'test'
    // Ensure pytest's color output renders sanely in Jenkins console.
    PY_COLORS       = '0'
    FORCE_COLOR     = '0'
  }

  stages {
    stage('Checkout') {
      steps { checkout scm }
    }

    stage('Tests') {
      // ``parallel`` here cuts wall-clock roughly in half — backend
      // pytest dominates (~3 min on a warm cache), frontend vitest is
      // ~60s. Failures in either branch fail the build.
      parallel {
        stage('Backend (pytest)') {
          stages {
            stage('Install deps') {
              steps {
                // ``py -3.11`` is the launcher selector on Windows. If
                // the agent has a different Python install, override
                // PYTHON_BIN at the global Jenkins env level.
                bat '''
                  py -3.11 -m venv backend\\.venv
                  call backend\\.venv\\Scripts\\activate.bat
                  python -m pip install --upgrade pip wheel
                  pip install -r backend\\requirements.txt
                  pip install pytest pytest-cov pytest-asyncio
                '''
              }
            }

            stage('Run pytest') {
              steps {
                // Exclude integration tests via ``-m "not integration"``.
                // The marker is registered in backend/pytest.ini so
                // this expression is portable across pytest versions.
                bat '''
                  call backend\\.venv\\Scripts\\activate.bat
                  cd backend
                  pytest tests/ ^
                    -m "not integration" ^
                    --ignore=tests/integration ^
                    --junitxml=test-results-unit.xml ^
                    --cov=app ^
                    --cov-report=xml:coverage.xml ^
                    --cov-report=term ^
                    -q
                '''
              }
              post {
                always {
                  // JUnit XML lands in Jenkins' Tests tab regardless of
                  // pass/fail. ``allowEmptyResults`` is false here so a
                  // missing file (= pytest didn't run) trips the build,
                  // which is the right signal — silent success on no
                  // tests would mask a CI-config regression.
                  junit testResults: 'backend/test-results-unit.xml',
                        allowEmptyResults: false,
                        skipPublishingChecks: true
                  archiveArtifacts artifacts: 'backend/coverage.xml',
                                   allowEmptyArchive: true
                }
              }
            }
          }
        }

        stage('Frontend (vitest)') {
          stages {
            stage('Install deps') {
              steps {
                // ``npm ci`` over ``npm install`` so a stale lockfile
                // surfaces as a hard error instead of silently drifting
                // dependency versions per build.
                bat '''
                  cd frontend
                  npm ci --no-audit --no-fund
                '''
              }
            }

            stage('Run vitest') {
              steps {
                // ``npm run test`` resolves to ``vitest run`` (one-shot)
                // — see frontend/package.json. The ``--reporter=junit``
                // emits an XML at the path passed to ``--outputFile``.
                bat '''
                  cd frontend
                  npm run test -- --reporter=default --reporter=junit --outputFile.junit=test-results-unit.xml
                '''
              }
              post {
                always {
                  junit testResults: 'frontend/test-results-unit.xml',
                        allowEmptyResults: false,
                        skipPublishingChecks: true
                }
              }
            }
          }
        }
      }
    }
  }

  post {
    failure {
      echo 'Unit tests failed. Check the Tests tab for the failing assertions, or the console log for import / setup errors.'
    }
  }
}
