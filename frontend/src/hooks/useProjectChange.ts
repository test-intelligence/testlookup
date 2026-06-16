import { useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'
import { useProjectStore } from '@/store/projectStore'

function usePreviousProjectId() {
  const activeProjectId = useProjectStore(s => s.activeProjectId)
  const previousProjectIdRef = useRef(activeProjectId)
  return { activeProjectId, previousProjectIdRef }
}

export function useProjectChangeRedirect(targetPath: string | null, enabled = true) {
  const navigate = useNavigate()
  const { activeProjectId, previousProjectIdRef } = usePreviousProjectId()

  useEffect(() => {
    if (!enabled) {
      previousProjectIdRef.current = activeProjectId
      return
    }

    const projectChanged = previousProjectIdRef.current !== activeProjectId
    previousProjectIdRef.current = activeProjectId

    if (projectChanged && targetPath) {
      navigate(targetPath, { replace: true })
    }
  }, [activeProjectId, enabled, navigate, targetPath, previousProjectIdRef])
}

export function useProjectChangeReset(onChange: () => void, enabled = true) {
  const { activeProjectId, previousProjectIdRef } = usePreviousProjectId()
  const onChangeRef = useRef(onChange)

  useEffect(() => {
    onChangeRef.current = onChange
  }, [onChange])

  useEffect(() => {
    if (!enabled) {
      previousProjectIdRef.current = activeProjectId
      return
    }

    const projectChanged = previousProjectIdRef.current !== activeProjectId
    previousProjectIdRef.current = activeProjectId

    if (projectChanged) {
      onChangeRef.current()
    }
  }, [activeProjectId, enabled, previousProjectIdRef])
}
