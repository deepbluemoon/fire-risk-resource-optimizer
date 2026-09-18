import { defineStore } from 'pinia';
import { api, today, type Freshness, type GradeRef } from '../services/api';

export const useAppStore = defineStore('app', {
  state: () => ({
    date: today(),
    freshness: null as Freshness | null,
    grades: [] as GradeRef[],
    batch: null as any,
    models: [] as any[],
    loaded: false,
  }),
  actions: {
    async bootstrap() {
      if (this.loaded) return;
      const [grades, batch, models] = await Promise.allSettled([
        api.grades(), api.batchStatus(), api.models(),
      ]);
      if (grades.status === 'fulfilled') this.grades = grades.value;
      if (batch.status === 'fulfilled') {
        this.batch = batch.value;
        if (batch.value?.targetDate) this.date = String(batch.value.targetDate).slice(0, 10);
      }
      if (models.status === 'fulfilled') this.models = models.value;
      this.loaded = true;
    },
    setFreshness(f: Freshness | null) { this.freshness = f; },
    activeModel() { return this.models.find((m) => m.isActive) ?? null; },
  },
});
