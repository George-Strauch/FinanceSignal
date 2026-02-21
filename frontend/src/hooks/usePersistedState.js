import { useState, useEffect, useRef } from 'react'

export default function usePersistedState(key, initialValue) {
  const isFirst = useRef(true)
  const [value, setValue] = useState(() => {
    try {
      const raw = localStorage.getItem(key)
      return raw != null ? JSON.parse(raw) : initialValue
    } catch {
      return initialValue
    }
  })

  useEffect(() => {
    if (isFirst.current) {
      isFirst.current = false
      return
    }
    try {
      localStorage.setItem(key, JSON.stringify(value))
    } catch {}
  }, [key, value])

  return [value, setValue]
}
