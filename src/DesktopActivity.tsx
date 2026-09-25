import { useEffect, useState } from 'react';
import { listen } from '@tauri-apps/api/event';

export default function DesktopActivity() {
  const [point, setPoint] = useState<{ x: number; y: number } | null>(null);
  useEffect(() => {
    const subscription = listen<{ x: number; y: number }>('opencore-desktop-pointer', event => {
      setPoint({ x: event.payload.x / window.devicePixelRatio, y: event.payload.y / window.devicePixelRatio });
    });
    return () => { void subscription.then(unlisten => unlisten()); };
  }, []);
  return <div className="desktop-activity-frame" aria-label="OpenCore is controlling this window">
    <div className="desktop-activity-label">OpenCore is using this window</div>
    {point && <img className="desktop-ai-pointer" src="/opencore-pointer.png" alt="OpenCore pointer" style={{ left: point.x, top: point.y }} />}
  </div>;
}
