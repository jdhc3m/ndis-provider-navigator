export { ANSWER } from './answer'
export { AMBIGUOUS } from './ambiguous'
export {
  FIRST_MESSAGE,
  FOLLOW_UP_MESSAGE,
  REPLY_1,
  REPLY_2,
  REPLY_REFUSAL,
  STANDALONE_QUESTION,
  THREAD,
  THREAD_FULL_DETAIL,
  THREAD_ID,
  THREAD_NOT_FOUND_DETAIL,
  TURN_1,
  TURN_2,
} from './chat'
export { EMPTY } from './empty'
export { LOCALITY } from './locality'
export { META, META_UNCONFIGURED } from './meta'
export { REFUSAL } from './refusal'
export { SERVICE, UNKNOWN_SERVICE } from './service'

/** The `detail` the API sends when the model endpoint cannot be reached. */
export const ERROR_DETAIL = 'intent parse failed: the model endpoint did not answer (fixture)'
export const ERROR_STATUS = 502
