import LegoActivityDashboard from './src/pages/LegoActivityDashboard'
import { I18nProvider } from './src/i18n'

export default function App() {
  return (
    <I18nProvider>
      <LegoActivityDashboard />
    </I18nProvider>
  )
}
