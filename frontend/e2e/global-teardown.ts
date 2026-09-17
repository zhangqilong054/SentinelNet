/** globalTeardown：杀掉残留的 uvicorn（若是复用的手动实例也会被清掉——跑完即清）。 */
import { ensureCleaned } from './helpers/server'

export default async function globalTeardown() {
  await ensureCleaned()
}
