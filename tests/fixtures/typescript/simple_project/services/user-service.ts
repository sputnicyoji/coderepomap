import { z } from 'zod';
import type { User, UserId } from '../types.js';
import { formatName } from '../utils/helpers.js';
import * as helpers from '../utils/helpers.js';
import { BaseService } from './base.js';

const schema = z.object({ id: z.string() });

export class UserService extends BaseService implements Disposable {
  private readonly users = new Map<UserId, User>();

  static create(): UserService {
    return new UserService();
  }

  async rename(id: UserId, first: string, last: string): Promise<string> {
    const name = formatName(first, last);
    return helpers.slugify(name);
  }

  dispose = (): void => {
    this.users.clear();
  };
}
