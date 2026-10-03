import { installBridgeHooks } from './bridge.mjs';
import configuration from './local-config.mjs';

/** @type {import('claude-code').Register} */
export const register = (on) => installBridgeHooks(on, configuration);
