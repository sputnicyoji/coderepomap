import './polyfill.js';
import { UserService } from './services';

export * from './types.js';

export function createService(): UserService {
  return UserService.create();
}
