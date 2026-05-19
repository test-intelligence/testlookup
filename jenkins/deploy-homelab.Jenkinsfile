// ============================================================
// Jenkins pipeline — Deploy TestLookup to the homelab K3s cluster
// ============================================================
//
// Wraps ``homelabsetup/deploy-homelab.sh`` (which already handles the
// BUILD_TAG substitution, image push, kubectl apply, admin-user
// creation, and rollout wait) and adds a post-deploy smoke check.
//
// Designed for a Jenkins running on the same Windows machine as the
// developer environment. The agent must have Git Bash + Docker
// Desktop + kubectl in PATH; the script delegates everything else.
//
// Wire this Jenkinsfile to a Pipeline job pointing at this repo
// with ``Script Path = jenkins/deploy-homelab.Jenkinsfile``.
// ============================================================

pipeline {
  // ``any`` works for single-agent installs. For a multi-agent farm,
  // label the Windows node "windows-homelab" and replace with
  // ``agent { label 'windows-homelab' }``.
  agent any

  options {
    // Deploys are long-running but should never wedge a CI runner.
    timeout(time: 30, unit: 'MINUTES')
    timestamps()
    // Only one deploy at a time. A second invocation while a deploy
    // is in flight would race on the BUILD_TAG_PLACEHOLDER substitution
    // in k8s/overlays/homelab/kustomization.yaml. See the
    // ``homelab.build-tag-placeholder`` quality gate.
    disableConcurrentBuilds()
    buildDiscarder(logRotator(numToKeepStr: '30'))
  }

  parameters {
    booleanParam(
      name: 'SKIP_BUILD',
      defaultValue: false,
      description: 'Skip docker build & push. Re-applies the manifests using the existing images.',
    )
    booleanParam(
      name: 'SKIP_REGISTRY',
      defaultValue: true,
      description: 'Skip in-cluster registry redeploy. Safe default — the registry rarely changes.',
    )
    booleanParam(
      name: 'SKIP_MODELS',
      defaultValue: true,
      description: 'Skip Ollama model pull. Safe default — models are cached on the PVC.',
    )
    booleanParam(
      name: 'SKIP_MIRROR_CHECK',
      defaultValue: false,
      description: 'Bypass the K3s containerd mirror precheck. Use ONLY when the homelab is offline or you have already verified registries.yaml. Default off.',
    )
    string(
      name: 'BACKEND_URL',
      defaultValue: 'http://testlookup.local',
      description: 'Public ingress URL probed during the post-deploy smoke check.',
    )
  }

  environment {
    // The script consults these via env, not as flags. KUBECONFIG points
    // at the homelab k3s admin config; set the credential under Jenkins →
    // Manage → Credentials → System → Add Credentials → "Secret file".
    KUBECONFIG = credentials('homelab-kubeconfig')
    // Optional override of the admin password the script bakes in.
    // Defaults to ``Admin@2026!`` if unset — the script handles that.
    ADMIN_PASSWORD = credentials('homelab-admin-password')
  }

  stages {
    stage('Checkout') {
      steps {
        checkout scm
        // Surface the commit being deployed in the build banner. The
        // deploy script stamps this into the BUILD_TAG via ``date +%s``
        // but the git SHA is what an operator usually correlates with.
        bat 'git log -1 --pretty=format:"%%h %%s"'
      }
    }

    stage('Preflight') {
      // Fail fast on missing tools BEFORE the deploy script gets halfway
      // through and leaves a substituted BUILD_TAG_PLACEHOLDER behind
      // (the EXIT trap restores it, but the failure mode is noisier).
      steps {
        bat '''
          @echo off
          where docker || (echo ERROR: docker not in PATH & exit /b 1)
          where kubectl || (echo ERROR: kubectl not in PATH & exit /b 1)
          where bash || (echo ERROR: bash not in PATH ^(install Git Bash^) & exit /b 1)
        '''
        bat 'bash -c "docker version --format \'{{.Server.Version}}\' && kubectl version --client --short"'
      }
    }

    stage('Deploy') {
      steps {
        script {
          // Assemble flag list. The script accepts the flags in any
          // order; we only forward the ones the user actually toggled
          // so logs stay clean.
          def flags = []
          if (params.SKIP_BUILD)        { flags << '--skip-build' }
          if (params.SKIP_REGISTRY)     { flags << '--skip-registry' }
          if (params.SKIP_MODELS)       { flags << '--skip-models' }
          if (params.SKIP_MIRROR_CHECK) { flags << '--skip-mirror-check' }
          // Always pass --skip-dns: Jenkins agents won't have anything
          // to add to a hosts file mid-run and the reminder is noise.
          flags << '--skip-dns'

          def flagStr = flags.join(' ')
          // The deploy script is bash + POSIX — invoke through Git Bash
          // explicitly so the cmd.exe shell doesn't mangle the script.
          bat "bash homelabsetup/deploy-homelab.sh ${flagStr}"
        }
      }
    }

    stage('Smoke check') {
      // Belt-and-braces: the deploy script already runs
      // ``kubectl rollout status`` on backend + frontend. Probing the
      // ingress here verifies the path *through* MetalLB + Traefik +
      // the nginx ConfigMap mount, which the rollout-status check
      // can't see.
      steps {
        retry(5) {
          bat """
            curl --fail --silent --show-error --max-time 10 ^
              "${params.BACKEND_URL}/health" ^
              || (timeout /t 10 /nobreak >nul & exit /b 1)
          """
        }
      }
    }
  }

  post {
    always {
      // Capture pod status so a failed build leaves enough breadcrumbs
      // to diagnose without the operator re-running ``kubectl get pods``
      // by hand. Tolerant of missing kubeconfig — preflight should have
      // caught that, but ``|| true`` keeps the post block from
      // overwriting the real failure cause in the log.
      bat 'kubectl -n testlookup get pods -o wide || exit /b 0'
      bat 'kubectl -n testlookup get ingress || exit /b 0'
    }
    success {
      echo "Deployed. Open ${params.BACKEND_URL} — admin / Admin@2026! (rotate via /settings/users)."
    }
    failure {
      // The deploy script ``trap``s a placeholder restore on EXIT,
      // but if Jenkins kills the process forcibly the restore may
      // not have fired. Surface that hint in the failure log so the
      // next run isn't confused by a polluted overlay.
      bat 'bash -c "git diff --name-only k8s/overlays/homelab/ || true"'
      echo "If k8s/overlays/homelab/kustomization.yaml shows a dirty diff above, run `git checkout -- k8s/overlays/homelab/kustomization.yaml` before re-running this job."
    }
  }
}
