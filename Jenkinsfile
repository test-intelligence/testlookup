pipeline {
  agent any

  options {
    timestamps()
    disableConcurrentBuilds()
    ansiColor('xterm')
    buildDiscarder(logRotator(numToKeepStr: '30'))
  }

  parameters {
    booleanParam(name: 'RUN_TESTS', defaultValue: true, description: 'Run backend/frontend/MCP validation stages')
    booleanParam(name: 'BUILD_IMAGES', defaultValue: true, description: 'Build production Docker images')
    booleanParam(name: 'PUSH_IMAGES', defaultValue: false, description: 'Push images to registry')
    string(name: 'DOCKER_REGISTRY', defaultValue: 'ghcr.io/your-org/testlookup', description: 'Registry/repository prefix (no trailing slash)')
    string(name: 'REGISTRY_CREDENTIALS_ID', defaultValue: 'docker-registry-creds', description: 'Jenkins username/password credentials ID for docker login')

    booleanParam(name: 'DEPLOY_TO_VM', defaultValue: false, description: 'Deploy to remote VM over SSH after successful build')
    string(name: 'VM_SSH_CREDENTIALS_ID', defaultValue: 'testlookup-vm-ssh', description: 'Jenkins SSH private key credentials ID')
    string(name: 'DEPLOY_SSH_TARGET', defaultValue: 'debian@YOUR_VM_IP', description: 'SSH target in user@host form')
    string(name: 'DEPLOY_PATH', defaultValue: '/home/debian/testlookup', description: 'Absolute repo path on remote VM')
    choice(name: 'DEPLOY_PROFILE', choices: ['standard', 'async'], description: 'Use async profile to start worker/beat services')
  }

  environment {
    COMPOSE_MAIN = 'docker compose -f docker-compose.yml'
    COMPOSE_GCP = 'docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml'
  }

  stages {
    stage('Checkout') {
      steps {
        checkout scm
      }
    }

    stage('Prepare Environment File') {
      steps {
        sh '''
          set -eux
          if [ ! -f .env ]; then
            cp .env.example .env
          fi
        '''
      }
    }

    stage('Validate Deployment Artifacts') {
      steps {
        sh '''
          set -eux
          docker compose version
          docker compose -f docker-compose.yml config > /tmp/compose.local.resolved.yaml
          docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml config > /tmp/compose.gcp.resolved.yaml
        '''
      }
    }

    stage('Test and Validate') {
      when {
        expression { return params.RUN_TESTS }
      }
      stages {
        stage('Start Test Stack') {
          steps {
            sh '''
              set -eux
              ${COMPOSE_MAIN} up -d --build
              ${COMPOSE_MAIN} exec -T backend alembic upgrade head
            '''
          }
        }

        stage('Backend Checks') {
          steps {
            sh '''
              set -eux
              ${COMPOSE_MAIN} exec -T backend ruff check app/ tests/
              ${COMPOSE_MAIN} exec -T backend mypy app/ --ignore-missing-imports || true
              ${COMPOSE_MAIN} exec -T backend pytest tests/ -v --tb=short --junit-xml=test-results.xml
            '''
          }
        }

        stage('Frontend Checks') {
          steps {
            sh '''
              set -eux
              ${COMPOSE_MAIN} exec -T frontend npm run lint
              ${COMPOSE_MAIN} exec -T frontend npm run type-check || true
              ${COMPOSE_MAIN} exec -T frontend npm run test
              ${COMPOSE_MAIN} exec -T frontend npm run build
            '''
          }
        }

        stage('MCP Checks') {
          steps {
            sh '''
              set -eux
              ${COMPOSE_MAIN} run --rm mcp python -m compileall -q .
              ${COMPOSE_MAIN} run --rm mcp python - <<'PY'
import sys
sys.path.insert(0, '.')
from config import settings
import client
from tools import auth, projects, runs, metrics, analytics, analysis, release
from resources import registry
from prompts import templates
print('MCP imports validated successfully')
PY
            '''
          }
        }
      }
    }

    stage('Build Images') {
      when {
        expression { return params.BUILD_IMAGES }
      }
      steps {
        script {
          env.IMAGE_TAG = "${env.BUILD_NUMBER}-${env.GIT_COMMIT.take(7)}"
        }
        sh '''
          set -eux
          docker build -t ${DOCKER_REGISTRY}/backend:${IMAGE_TAG} --target production ./backend
          docker build -t ${DOCKER_REGISTRY}/frontend:${IMAGE_TAG} --target production ./frontend
          docker build -t ${DOCKER_REGISTRY}/mcp:${IMAGE_TAG} ./mcp
        '''
      }
    }

    stage('Push Images') {
      when {
        expression { return params.BUILD_IMAGES && params.PUSH_IMAGES }
      }
      steps {
        withCredentials([usernamePassword(credentialsId: params.REGISTRY_CREDENTIALS_ID, usernameVariable: 'REG_USER', passwordVariable: 'REG_PASS')]) {
          sh '''
            set -eux
            echo "$REG_PASS" | docker login -u "$REG_USER" --password-stdin $(echo ${DOCKER_REGISTRY} | cut -d/ -f1)
            docker push ${DOCKER_REGISTRY}/backend:${IMAGE_TAG}
            docker push ${DOCKER_REGISTRY}/frontend:${IMAGE_TAG}
            docker push ${DOCKER_REGISTRY}/mcp:${IMAGE_TAG}
          '''
        }
      }
    }

    stage('Deploy to GCP VM') {
      when {
        expression { return params.DEPLOY_TO_VM }
      }
      steps {
        sshagent(credentials: [params.VM_SSH_CREDENTIALS_ID]) {
          sh '''
            set -eux
            ssh -o StrictHostKeyChecking=no ${DEPLOY_SSH_TARGET} "\
              set -eux; \
              cd ${DEPLOY_PATH}; \
              git fetch --all --prune; \
              git checkout ${BRANCH_NAME}; \
              git pull --ff-only origin ${BRANCH_NAME}; \
              docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml up -d --build; \
              docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml exec -T backend alembic upgrade head; \
              if [ '${DEPLOY_PROFILE}' = 'async' ]; then \
                docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml --profile async up -d worker beat; \
              fi; \
              docker compose -f docker-compose.yml -f docker-compose.gcp-vm.yml ps \
            "
          '''
        }
      }
    }
  }

  post {
    always {
      junit allowEmptyResults: true, testResults: 'backend/test-results.xml'
      sh '''
        set +e
        ${COMPOSE_MAIN} down --remove-orphans
      '''
      cleanWs(deleteDirs: true)
    }
  }
}

