/** globalSetup：已有健康实例则复用（供手动调试），否则拉起隔离实例。 */
import { isHealthy, startServer } from './helpers/server'

export default async function globalSetup() {
  if (!(await isHealthy())) {
    await startServer()
  }
}
