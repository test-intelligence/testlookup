import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useProjectStore } from '@/store/projectStore'

function usePreviousProjectId() {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const previousProjectId = useRef(activeProjectId)
  return { activeProjectId, previousProjectId }
}

export function useProjectChangeRedirect(targetPath: string | null, enabled = true) {
  const navigate = useNavigate()
  const { activeProjectId, previousProjectId } = usePreviousProjectId()

  useEffect(() => {
    if (!enabled) {
      previousProjectId.current = activeProjectId
      return
    }

    const projectChanged = previousProjectId.current !== activeProjectId
    previousProjectId.current = activeProjectId

    if (projectChanged && targetPath) {
      navigate(targetPath, { replace: true })
    }
  }, [activeProjectId, enabled, navigate, targetPath, previousProjectId])
}

export function useProjectChangeReset(onChange: () => void, enabled = true) {
  const { activeProjectId, previousProjectId } = usePreviousProjectId()
  const onChangeRef = useRef(onChange)

  useEffect(() => {
    onChangeRef.current = onChange
  }, [onChange])

  useEffect(() => {
    if (!enabled) {
      previousProjectId.current = activeProjectId
      return
    }

    const projectChanged = previousProjectId.current !== activeProjectId
    previousProjectId.current = activeProjectId

    if (projectChanged) {
      onChangeRef.current()
    }
  }, [activeProjectId, enabled, previousProjectId])
}
