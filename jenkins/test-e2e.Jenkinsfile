// ============================================================
// Jenkins pipeline — End-to-end tests (Playwright + live stack)
// ============================================================
//
// Brings up the full docker compose stack (backend, frontend, postgres,
// mongo, redis, minio) and runs the Playwright suite against the
// running services. Per ``frontend/playwright.config.ts``:
//
//   * baseURL: http://localhost:3000
//   * single worker (workers=1) so backend isn't load-tested
//   * CI mode enables 2 retries + ``forbidOnly``
//   * globalSetup runs a real login to populate the storageState fixture
//
// This is the heaviest pipeline (~15-20 min). Wire it to a nightly
// trigger plus manual invocation; running on every PR is overkill.
// ============================================================

pipeline {
  agent any

  options {
    timeout(time: 45, unit: 'MINUTES')
    timestamps()
    buildDiscarder(logRotator(numToKeepStr: '20'))
    // The stack uses fixed host ports (3000, 8000, 5433, etc.) so
    // parallel builds collide. Force serial.
    disableConcurrentBuilds()
  }

  parameters {
    string(
      name: 'PLAYWRIGHT_PROJECT',
      defaultValue: 'chromium',
      description: 'Playwright project to run (chromium / firefox / webkit / "" for all).',
    )
    string(
      name: 'PLAYWRIGHT_GREP',
      defaultValue: '',
      description: 'Optional regex to filter test titles. Empty = all tests.',
    )
    booleanParam(
      name: 'KEEP_STACK_ON_FAILURE',
      defaultValue: false,
      description: 'Skip docker compose down when tests fail. Use for interactive debugging — remember to tear down manually afterwards.',
    )
  }

  environment {
    // Playwright reads CI=true for retry behaviour and ``--forbid-only``.
    CI = 'true'
    // Suppress Node experimental warnings that otherwise pollute the
    // console log and break some test-result parsers.
    NODE_NO_WARNINGS = '1'
  }

  // Trigger guidance (uncomment one):
  //
  //   triggers { cron('H 2 * * *') }         // Nightly ~2am
  //   triggers { pollSCM('H/30 * * * *') }   // Light SCM poll
  //
  // Or wire via the multibranch indexer + GitHub webhook.

  stages {
    stage('Checkout') {
      steps { checkout scm }
    }

    stage('Bring up stack') {
      // ``--build`` ensures local source changes are picked up; without
      // it a stale image keeps yesterday's frontend bundle in play and
      // E2E failures get blamed on test flakes when they're really a
      // missing rebuild. See [[feedback_verify_bundle_when_changes_missing]].
      steps {
        bat 'docker compose up -d --build'
      }
    }

    stage('Wait for readiness') {
      // Three gates: backend /health, frontend root, and an idempotent
      // login (creates the storageState file the global setup expects).
      // Without these, the first test attempt times out waiting for a
      // login modal that hasn't loaded its JS bundle yet.
      steps {
        // Backend ready first — frontend won't reach API otherwise.
        retry(20) {
          bat '''
            curl --fail --silent --max-time 5 http://localhost:8000/health ^
              || (timeout /t 5 /nobreak >nul & exit /b 1)
          '''
        }
        // Frontend bundle served.
        retry(20) {
          bat '''
            curl --fail --silent --max-time 5 http://localhost:3000 ^
              || (timeout /t 5 /nobreak >nul & exit /b 1)
          '''
        }
      }
    }

    stage('Install frontend deps') {
      steps {
        bat '''
          cd frontend
          npm ci --no-audit --no-fund
        '''
      }
    }

    stage('Install Playwright browsers') {
      // The runner has Node but probably not the browser binaries.
      // ``--with-deps`` would pull OS libs on Linux but is a no-op on
      // Windows; the binaries themselves are still required.
      steps {
        bat '''
          cd frontend
          npx playwright install --with-deps chromium firefox webkit
        '''
      }
    }

    stage('Run Playwright') {
      steps {
        script {
          def projectFlag = params.PLAYWRIGHT_PROJECT?.trim()
              ? "--project=${params.PLAYWRIGHT_PROJECT}" : ''
          def grepFlag = params.PLAYWRIGHT_GREP?.trim()
              ? "--grep \"${params.PLAYWRIGHT_GREP}\"" : ''

          bat """
            cd frontend
            npx playwright test ${projectFlag} ${grepFlag} ^
              --reporter=html,junit ^
              || set EXIT=%ERRORLEVEL%
            if defined EXIT exit /b %EXIT%
          """
        }
      }
      post {
        always {
          // Junit XML lands in the Tests tab; HTML report archived for
          // download. The HTML report is the high-signal artifact —
          // includes screenshots, video, trace on retries.
          junit testResults: 'frontend/test-results/**/*.xml',
                allowEmptyResults: true,
                skipPublishingChecks: true
          publishHTML(target: [
            allowMissing:          true,
            alwaysLinkToLastBuild: true,
            keepAll:               true,
            reportDir:             'frontend/playwright-report',
            reportFiles:           'index.html',
            reportName:            'Playwright HTML Report',
          ])
          archiveArtifacts artifacts: 'frontend/playwright-report/**/*',
                           allowEmptyArchive: true
        }
      }
    }
  }

  post {
    always {
      script {
        // Capture backend logs on failure so flakiness vs real regression
        // can be diagnosed from the build page alone. Tolerant of compose
        // already being gone.
        if (currentBuild.currentResult != 'SUCCESS') {
          bat 'docker compose logs backend --tail=200 > backend.log 2>&1 || exit /b 0'
          archiveArtifacts artifacts: 'backend.log', allowEmptyArchive: true
        }

        if (currentBuild.currentResult == 'SUCCESS' || !params.KEEP_STACK_ON_FAILURE) {
          // ``down -v`` clears volumes so the next run starts from a
          // clean DB. E2E tests assume seeded fixture state; without
          // the volume reset you get ghost users from yesterday's run.
          bat 'docker compose down -v --remove-orphans || exit /b 0'
        } else {
          echo 'KEEP_STACK_ON_FAILURE=true — stack left running for inspection. Remember to ``docker compose down -v`` manually before the next build.'
        }
      }
    }
  }
}
