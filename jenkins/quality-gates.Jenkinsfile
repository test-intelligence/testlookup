// ============================================================
// Jenkins pipeline — Quality gates
// ============================================================
//
// Runs the cross-cutting invariant guards in ``scripts/quality_gate.py``
// (14 guards as of this writing — backend / frontend / database /
// agents / homelab). Pure file IO, no backend/frontend deps, no
// containers — finishes in seconds.
//
// Also runs the gate's own self-test (``scripts/test_quality_gate.py``)
// so a careless edit to ``quality_gate.py`` that accidentally turns
// a guard into a no-op fails CI loudly.
//
// Best wired as a required check on every PR. Cheap enough that
// running it twice (per-PR + on-merge) costs nothing.
// ============================================================

pipeline {
  agent any

  options {
    // Generous because Python startup on a cold-cache Windows agent
    // can dominate. The actual gate run is sub-second.
    timeout(time: 10, unit: 'MINUTES')
    timestamps()
    buildDiscarder(logRotator(numToKeepStr: '100'))
  }

  environment {
    // The gate is pure file IO — none of these are strictly required,
    // but ``quality_gate.py`` imports ``app.core.config`` transitively
    // via none of the guards directly; the empty env keeps the script
    // hermetic and prevents future drift.
    PYTHONUTF8       = '1'
    PYTHONIOENCODING = 'utf-8'
    PY_COLORS        = '0'
    FORCE_COLOR      = '0'
  }

  stages {
    stage('Checkout') {
      steps { checkout scm }
    }

    stage('Install pytest') {
      // The gate itself runs on the system Python — no deps. The
      // self-test needs pytest. Install in a venv to keep the agent
      // tidy across pipelines that may want different pytest versions.
      steps {
        bat '''
          py -3.11 -m venv .qg-venv
          call .qg-venv\\Scripts\\activate.bat
          python -m pip install --upgrade pip wheel
          pip install pytest
        '''
      }
    }

    stage('Run quality_gate.py') {
      // Non-zero exit on any new violation. Stale-baseline warnings
      // are printed but don't fail (intentional — the ratchet only
      // tightens via PRs to the baseline file, not via CI).
      steps {
        bat '''
          call .qg-venv\\Scripts\\activate.bat
          python scripts\\quality_gate.py
        '''
      }
    }

    stage('Self-test the gate') {
      // Pins the ratchet semantics so a guard can't be silently
      // disabled by editing scripts/quality_gate.py.
      steps {
        bat '''
          call .qg-venv\\Scripts\\activate.bat
          python -m pytest scripts\\test_quality_gate.py ^
            --junitxml=test-results-quality-gate.xml ^
            -q
        '''
      }
      post {
        always {
          junit testResults: 'test-results-quality-gate.xml',
                allowEmptyResults: false,
                skipPublishingChecks: true
        }
      }
    }
  }

  post {
    failure {
      echo """
        Quality gate failed. To diagnose:

          1. Look at the console log above for ``FAIL <guard.name>``
             lines. Each names the file:line of the new violation and
             gives a one-line fix hint.

          2. If the violation is *legitimate* (rare — usually you fix
             the code instead), run the following locally and commit
             the baseline diff:

               python scripts\\quality_gate.py --only <guard.name> --update-baseline

             Baseline edits are reviewed in PR — the ratchet only
             tightens via PR, not via this CI job.

          3. Stale-baseline warnings (``!!``) are non-fatal but indicate
             the code is cleaner than the baseline claims. Run
             ``--update-baseline`` for that guard to prune.
      """.stripIndent()
    }
  }
}
