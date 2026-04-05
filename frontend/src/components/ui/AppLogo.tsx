import { useState } from 'react'

interface AppLogoProps {
  className?: string
  fallbackClassName?: string
}

export default function AppLogo({
  className = 'h-12 w-auto',
  fallbackClassName,
}: AppLogoProps) {
  const [failed, setFailed] = useState(false)

  if (failed) {
    return (
      <span
        className={
          fallbackClassName ??
          'text-lg font-bold bg-gradient-to-r from-teal-400 to-teal-200 bg-clip-text text-transparent'
        }
      >
        testlookup
      </span>
    )
  }

  return (
    <img
      src="/testLookup_Logo.png"
      alt="TestLookup"
      className={className}
      onError={() => setFailed(true)}
    />
  )
}
